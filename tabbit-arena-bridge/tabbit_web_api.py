#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tabbit_web_api.py — 把 Tabbit 的网页对话（web.tabbit.ai/session/…）包装成一个
**OpenAI 兼容的本地接口**（POST /v1/chat/completions），底层仍然是 bridge.py 的 CDP 逻辑：
    收到请求 → 把"最后一条 user 消息"打进 Tabbit 对话页的输入框 → 等 AI 回复稳定 → 原样返回。

这样一来，Arena 侧用现成的 ArenaAgentBridge（http://127.0.0.1:8000/v1），
Tabbit 侧用本脚本（http://127.0.0.1:8124/v1），中间用 ai-pingpong/orchestrator.py 来回转发，
三个部件都只是"OpenAI 接口"，谁坏了换谁，互不影响。

用法：
    python tabbit_web_api.py --config config.json                 # 默认取 config.json 里的 "tabbit" 一节
    python tabbit_web_api.py --config config.json --port 8124 --cdp http://127.0.0.1:9223
    python tabbit_web_api.py --config config.json --side arena    # 也能包装 Arena 页面（备用，不推荐）

测试：
    curl http://127.0.0.1:8124/v1/models
    curl http://127.0.0.1:8124/v1/chat/completions -H "Content-Type: application/json" ^
         -d "{\"model\":\"tabbit-web\",\"messages\":[{\"role\":\"user\",\"content\":\"请只回复：bridge ok\"}]}"

约定：
    * 页面本身保存上下文，所以默认只把 **最后一条 user 消息** 发进去（不重复发历史）。
      需要整段历史时，在请求体里加 "x_full_transcript": true。
    * 一次只处理一个请求；并发的第二个请求直接得到 429（queue_full）。
    * 请求体里的 "timeout"（秒）可覆盖 config 的 max_wait_seconds。
    * 页面若弹出需要人工选择/回答的组件（config 的 human_needed），回复末尾会追加 --human-token
      （默认 "[NEED_HUMAN]"），编排器看到就会停机通知你。
    * 返回体多一个 "x_bridge" 字段：{"human_widget":…, "elapsed_s":…, "target":…}。
"""
import argparse
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge  # noqa: E402  同目录的 bridge.py（v0.5+）

VERSION = "0.1"
try:
    sys.stdout.reconfigure(errors="replace")   # Windows 旧控制台也别因为编码崩掉
except Exception:
    pass


def now():
    return time.strftime("%H:%M:%S")


class PageSession:
    """管理与一个页面目标的 CDP 连接；掉线/页面刷新后自动重连。"""

    def __init__(self, side_name: str, side_cfg: dict, cdp_url: str):
        self.side_name, self.cfg, self.cdp_url = side_name, dict(side_cfg), cdp_url
        self.side = None
        self.lock = threading.Lock()            # 单飞：一次只让一个请求碰页面
        self.state = {"attached": False, "target": None, "last_error": None, "requests": 0, "in_flight": False}

    # ---- 连接 -------------------------------------------------------------
    def attach(self):
        try:
            info = bridge.pick_target(bridge.list_targets(self.cdp_url), self.cfg["target_match"])
        except SystemExit as e:                  # bridge.py 里用 SystemExit 报"连不上/找不到"
            raise ConnectionError(str(e)) from None
        if self.side is not None:
            self.side.t.close()
        self.side = bridge.Side(self.side_name, dict(self.cfg), bridge.Target(info))   # 传副本：单次请求的 timeout 覆盖不能污染基础配置
        self.state.update(attached=True, target=f"[{info.get('type')}] {info.get('title')!r} {info.get('url')}")
        print(f"[{now()}] attached: {self.state['target']}", flush=True)

    def ensure(self):
        if self.side is None:
            self.attach()
            return
        try:
            self.side.t.eval("1+1")
        except Exception:
            print(f"[{now()}] 连接失效，重新附着…", flush=True)
            self.side = None
            self.attach()

    # ---- 一问一答 -----------------------------------------------------------
    def ask(self, text: str, timeout: float | None = None) -> dict:
        self.ensure()
        if timeout:
            self.side.cfg["max_wait_seconds"] = float(timeout)
        else:
            self.side.cfg["max_wait_seconds"] = float(self.cfg.get("max_wait_seconds", 900))
        t0 = time.time()
        prev = self.side.snapshot()               # 发送前的 (条数, 最后一条文本)，用来判断"新回复"
        self.side.send(text)
        reply = self.side.wait_for_reply(*prev)
        human = False
        try:
            human = bool(self.side.human_widget())
        except Exception:
            pass
        return {"text": reply, "human_widget": human, "elapsed_s": round(time.time() - t0, 1)}


SESSION: PageSession | None = None
OPTS = {"model": "tabbit-web", "human_token": "[NEED_HUMAN]"}


def extract_text(messages: list, full: bool) -> str:
    def as_text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type", "text") == "text")
        if isinstance(content, dict):
            return content.get("text", "")
        return ""
    if full:
        return "\n\n".join(f"### {m.get('role')}\n{as_text(m.get('content'))}".strip() for m in messages if as_text(m.get("content")).strip())
    for m in reversed(messages):
        if m.get("role") == "user" and as_text(m.get("content")).strip():
            return as_text(m.get("content"))
    for m in reversed(messages):
        if as_text(m.get("content")).strip():
            return as_text(m.get("content"))
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = f"tabbit-web-api/{VERSION}"

    def log_message(self, fmt, *args):          # 只保留我们自己的日志
        pass

    # ---- 工具 ----------------------------------------------------------------
    def _json(self, code: int, obj: dict, extra_headers: dict | None = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str, etype: str):
        self._json(code, {"error": {"message": message, "type": etype, "code": etype}})

    def _sse(self, content: str, model: str, rid: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        chunk = {"id": rid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]}
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        end = dict(chunk); end["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        self.wfile.write(f"data: {json.dumps(end, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode("utf-8"))

    # ---- 路由 ----------------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/v1/models", "/models"):
            return self._json(200, {"object": "list", "data": [{"id": OPTS["model"], "object": "model", "owned_by": "tabbit-web-api"}]})
        if path in ("/", "/healthz", "/readyz", "/v1/bridge/status"):
            st = dict(SESSION.state) if SESSION else {}
            st.update(version=VERSION, side=SESSION.side_name if SESSION else None, cdp=SESSION.cdp_url if SESSION else None)
            code = 200 if (path != "/readyz" or st.get("attached")) else 503
            return self._json(code, st)
        self._error(404, f"no route {path}", "not_found")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/v1/chat/completions", "/chat/completions"):
            return self._error(404, f"no route {path}", "not_found")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception as e:
            return self._error(400, f"bad json: {e}", "invalid_request_error")
        messages = req.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return self._error(400, "`messages` must be a non-empty list", "invalid_request_error")
        text = extract_text(messages, bool(req.get("x_full_transcript")))
        if not text.strip():
            return self._error(400, "no text content to send", "invalid_request_error")
        timeout = req.get("timeout")
        if isinstance(timeout, dict):
            timeout = timeout.get("total")
        try:
            timeout = float(timeout) if timeout else None
        except (TypeError, ValueError):
            timeout = None
        model = req.get("model") or OPTS["model"]
        rid = "chatcmpl-" + uuid.uuid4().hex[:24]

        if not SESSION.lock.acquire(blocking=False):
            return self._error(429, "another request is in flight (the page can only do one at a time)", "queue_full")
        try:
            SESSION.state.update(in_flight=True, requests=SESSION.state["requests"] + 1)
            print(f"[{now()}] -> 发送 {len(text)} 字（timeout={timeout or SESSION.cfg.get('max_wait_seconds')}s）: {text[:80]!r}", flush=True)
            try:
                res = SESSION.ask(text, timeout)
            except ConnectionError as e:
                SESSION.state.update(attached=False, last_error=str(e))
                print(f"[{now()}] !! browser_offline: {e}", flush=True)
                return self._error(503, str(e), "browser_offline")
            except TimeoutError as e:
                SESSION.state["last_error"] = str(e)
                print(f"[{now()}] !! page_timeout: {e}", flush=True)
                return self._error(504, str(e), "page_timeout")
            except RuntimeError as e:
                SESSION.state["last_error"] = str(e)
                print(f"[{now()}] !! dom_changed/send_failed: {e}", flush=True)
                return self._error(502, str(e), "dom_changed")
            except Exception as e:  # noqa: BLE001
                SESSION.state["last_error"] = repr(e)
                print(f"[{now()}] !! internal: {e!r}", flush=True)
                return self._error(500, repr(e), "bridge_error")
        finally:
            SESSION.state["in_flight"] = False
            SESSION.lock.release()

        content = res["text"]
        if res["human_widget"] and OPTS["human_token"] and OPTS["human_token"] not in content:
            content += f"\n\n{OPTS['human_token']} （页面出现了需要人工选择/回答的组件）"
        print(f"[{now()}] <- 回复 {len(content)} 字，用时 {res['elapsed_s']}s: {content[:80]!r}", flush=True)
        SESSION.state["last_error"] = None

        if req.get("stream") in (True, "true", "True", 1):
            return self._sse(content, model, rid)
        out = {
            "id": rid, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(text) // 2, "completion_tokens": len(content) // 2, "total_tokens": (len(text) + len(content)) // 2},
            "x_bridge": {"human_widget": res["human_widget"], "elapsed_s": res["elapsed_s"], "target": SESSION.state.get("target")},
        }
        self._json(200, out)


def main():
    global SESSION
    ap = argparse.ArgumentParser(description="把 Tabbit 网页对话包装成 OpenAI 兼容接口（CDP）")
    ap.add_argument("--version", action="version", version=f"tabbit_web_api.py {VERSION} (bridge.py {bridge.VERSION})")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--side", default="tabbit", help="用 config 里哪一节的选择器（默认 tabbit）")
    ap.add_argument("--cdp", default=None, help="远程调试地址，默认取 config 的 cdp_url / 环境变量 CDP_URL")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8124)
    ap.add_argument("--model", default="tabbit-web", help="/v1/models 里报告的模型名（随便起）")
    ap.add_argument("--human-token", default="[NEED_HUMAN]", help="页面出现人工组件时追加到回复末尾的标记；空字符串=不追加")
    ap.add_argument("--no-attach", action="store_true", help="启动时不立刻连页面（第一次请求时再连）")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.side not in cfg:
        sys.exit(f"config 里没有 '{args.side}' 一节")
    cdp = bridge.resolve_cdp(args, cfg)
    OPTS.update(model=args.model, human_token=args.human_token)
    SESSION = PageSession(args.side, cfg[args.side], cdp)
    print(f"tabbit_web_api.py v{VERSION} · side={args.side} · cdp={cdp} · target_match={cfg[args.side]['target_match']!r}", flush=True)
    if not args.no_attach:
        try:
            SESSION.attach()
        except ConnectionError as e:
            print(f"[warn] 暂时连不上页面（{e}），会在第一次请求时重试。", flush=True)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"监听 http://{args.host}:{args.port}/v1  （模型名 {args.model}；Ctrl+C 退出）", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        if SESSION and SESSION.side:
            SESSION.side.t.close()


if __name__ == "__main__":
    main()
