#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doctor.py — 一条命令说清楚"为什么发不出去 / 为什么 Send 是灰的"。

它会依次检查：
  1. ArenaAgentBridge 服务器（:8000）活没活、浏览器扩展连没连上、有没有请求卡在里面（Send 置灰的原因）
  2. 最近 5 条请求的结果（status / error_code / stop_reason）
  3. 通过 Tabbit 调试端口（:9223）看 Arena 页面此刻的状态：问卷在不在、输入框/发送按钮在不在、Agent 在不在跑
  4. 扩展的 dist\\chrome 里有没有我们的补丁（--aab-repo 指到 ArenaAgentBridge 目录）
最后给出"下一步该做什么"。

用法：
    python doctor.py                                   # 默认 :8000 / :9223 / 自动找 arena-agent-bridge（C:\\ai 下或本仓库旁边）
    python doctor.py --aab-repo D:\\path\\to\\arena-agent-bridge
    python doctor.py --fix                             # 顺手把此刻挂着的问卷点掉（同 survey_clicker 的阶梯）
    python doctor.py --cancel                          # 顺手把卡住的请求取消掉（等于面板 Browser 区域的 Cancel）
    python doctor.py --out doctor.json                 # 把原始数据也存一份，方便发给 AI 看
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge  # noqa: E402
import survey_clicker as sc  # noqa: E402

VERSION = "0.2"
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass
import platform  # noqa: E402
if platform.system() == "Windows":          # 中文 Windows 的控制台字体常没有这些符号
    OK, BAD, WAIT, DONE = "[OK]", "[X]", "[..]", "[OK]"
else:
    OK, BAD, WAIT, DONE = "✓", "✗", "⏳", "✔"
HERE = Path(__file__).resolve().parent


def default_aab_repo() -> str:
    """按顺序猜 ArenaAgentBridge 的位置：C:\\ai\\arena-agent-bridge → 本仓库上两级旁边 → 旧机的 G:\\ → 环境变量 AAB_REPO"""
    cands = [os.environ.get("AAB_REPO", ""), r"C:\ai\arena-agent-bridge", str(HERE.parent.parent / "arena-agent-bridge"),
             str(HERE.parent / "arena-agent-bridge"), r"G:\arena-agent-bridge"]
    for c in cands:
        if c and (Path(c) / "server").exists():
            return c
    return r"C:\ai\arena-agent-bridge"

JS_PAGE = r"""
(() => {
  const vis = (e) => { if (!e) return false; const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity !== 0; };
  const txt = (e) => (e.textContent || '').replace(/\s+/g, ' ').trim();
  const btns = [...document.querySelectorAll('button, [role="button"]')].filter(vis);
  const byText = (words) => btns.filter((b) => words.some((w) => txt(b).toLowerCase().startsWith(w.toLowerCase())));
  const byLabel = (re) => btns.filter((b) => re.test((b.getAttribute('aria-label') || '')));
  const ta = [...document.querySelectorAll('textarea, [contenteditable="true"]')].filter(vis).filter((e) => !/recaptcha/.test(e.id || ''));
  const send = document.querySelector('button[aria-label="Send message"]');
  const msgs = document.querySelectorAll('[data-agent-transcript-message]');
  const users = document.querySelectorAll('[data-user-message-layout]');
  return {
    url: location.href, title: document.title,
    keepWorking: byText(['继续工作', 'keep working', 'keep going', 'continue working']).length,
    surveyTitle: /此任务成功了吗|Was this task successful/.test(document.body.innerText || ''),
    input: ta.length ? (ta[0].tagName.toLowerCase() + ' (' + (ta[0].value || ta[0].textContent || '').length + ' 字)') : null,
    send: send ? (vis(send) ? (send.disabled ? 'visible-disabled' : 'visible') : 'hidden') : null,
    stop: byLabel(/stop/i).length + byText(['停止', 'stop']).length,
    messages: msgs.length, userMessages: users.length,
    login: byText(['登录', 'sign in', 'log in']).length,
    lastText: msgs.length ? txt(msgs[msgs.length - 1]).slice(-120) : null,
    amd: document.querySelectorAll('[id^="amd-"]').length,
  };
})()
"""

MARKERS = ("[tabbit-arena-bridge] pre-survey", "[tabbit-arena-bridge] robust-click", "[tabbit-arena-bridge] escape-fallback")


def get(url, timeout=5):
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
    except requests.RequestException as e:
        return None, e.__class__.__name__


def post(url, body=None, timeout=10):
    try:
        r = requests.post(url, json=body or {}, timeout=timeout)
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
    except requests.RequestException as e:
        return None, e.__class__.__name__


def h(title):
    print(f"\n== {title} " + "=" * max(0, 60 - len(title)))


def main():
    ap = argparse.ArgumentParser(description="ArenaAgentBridge + Tabbit 一键诊断")
    ap.add_argument("--version", action="version", version=f"doctor.py {VERSION}")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--cdp", default=None)
    ap.add_argument("--target", default="arena.ai/agent")
    ap.add_argument("--aab-repo", default=default_aab_repo(), help="ArenaAgentBridge 目录（默认自动猜：C:\\ai\\arena-agent-bridge 等）")
    ap.add_argument("--fix", action="store_true", help="把此刻挂着的问卷点掉")
    ap.add_argument("--cancel", action="store_true", help="取消卡住的请求")
    ap.add_argument("--out", default=None)
    ap.add_argument("--text", default="继续工作")
    ap.add_argument("--title", default="此任务成功了吗")
    args = ap.parse_args()
    cfg = {}
    try:  # 同目录 config.json 的 cdp_url 作为默认（你的 Tabbit 是 9223）
        cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    cdp = bridge.resolve_cdp(args, cfg)
    raw = {"at": time.strftime("%Y-%m-%d %H:%M:%S")}
    advice = []          # (级别, 文字)
    print(f"doctor.py v{VERSION} · server={args.server} · cdp={cdp} · aab={args.aab_repo}")

    # ------------------------------------------------------------- 1. 服务器
    h("1. ArenaAgentBridge 服务器")
    code, health = get(f"{args.server}/healthz")
    raw["healthz"] = health
    if code != 200:
        print(f"{BAD} 连不上 {args.server}（{health}）")
        advice.append(("A", f"服务器没在跑。到 ArenaAgentBridge 目录：`.\\.venv\\Scripts\\Activate.ps1; python -m server`，"
                            f"看到 Uvicorn running on http://127.0.0.1:8000 再继续。"))
        status = None
    else:
        print(f"{OK} 服务器在跑：v{health.get('version')}，已运行 {health.get('uptime_s', 0) / 60:.0f} 分钟")
        code, status = get(f"{args.server}/v1/bridge/status")
        raw["status"] = status
        if code != 200 or not isinstance(status, dict):
            print(f"  ! /v1/bridge/status 返回 {code}（{str(status)[:80]}）——若开了 AAB_REQUIRE_API_KEY 请先关掉再诊断")
            status = None
    connected = False
    pending = []
    if status:
        br = status.get("browser", {})
        connected = bool(br.get("connected"))
        clients = br.get("clients", [])
        if connected:
            for c in clients:
                print(f"{OK} 浏览器扩展已连接：{c.get('client')} v{c.get('version')}  state={c.get('state')} busy={c.get('busy')} "
                      f"最近心跳 {c.get('last_seen_ago_s')}s 前\n    页面: {c.get('url')}")
                if c.get("url") and "arena.ai/agent" not in str(c.get("url")):
                    advice.append(("B", f"扩展连的页面不是 arena.ai/agent（{c.get('url')}），请到正确的会话页刷新一次。"))
        else:
            print("{BAD} 浏览器扩展没连上（Overview 会显示 no browser attached）")
            advice.append(("A", "扩展没连上服务器：① Tabbit 里 chrome://extensions 确认扩展已启用、路径是 …\\dist\\chrome；"
                                "② 扩展点过“重新加载”之后必须 **刷新 Arena 标签页**；③ 弹窗 Status 页签点 Reconnect。"))
        sv = status.get("server", {})
        pending = sv.get("pending", []) or []
        print(f"  队列: pending={sv.get('pending_requests')} queue_depth={sv.get('queue_depth')}/{sv.get('queue_max')}")
        for p in pending:
            running = p.get("running_for_s")
            print(f"  {WAIT} 请求 {p.get('id')} mode={p.get('mode')} 排队 {p.get('queued_for_s')}s"
                  f"{'，页面里已跑 ' + str(running) + 's' if running is not None else '（还没发进页面）'}\n"
                  f"     内容: {str(p.get('preview') or p.get('prompt_preview') or '')[:80]!r}")
        if pending:
            advice.append(("A", f"有 {len(pending)} 条请求还没结束——**Playground 的 Send 置灰就是因为它**（一次只跑一条）。"
                                "它要么真的在等 Agent 回答，要么被问卷挡住没发出去（见第 3 节）。"
                                "要放弃它：`python doctor.py --cancel`，或面板 Browser 区域点 Cancel。"))
        errs = status.get("recent_errors") or []
        raw["recent_errors"] = errs
        if errs:
            print("  最近错误:")
            for e in errs[-3:]:
                print(f"    - {json.dumps(e, ensure_ascii=False)[:160]}")

    # ------------------------------------------------------------- 2. 历史
    if status is not None:
        h("2. 最近 5 条请求")
        code, hist = get(f"{args.server}/admin/api/history?limit=5")
        raw["history"] = hist if code == 200 else None
        if code == 200 and isinstance(hist, dict):
            items = hist.get("items", [])
            if not items:
                print("  （还没有任何请求）")
            for it in items:
                age = it.get("age_s", 0)
                line = (f"  {int(age // 60):3d} 分钟前  {it.get('status'):8s} {str(it.get('error_code') or ''):16s} "
                        f"stop={it.get('stop_reason') or '-':8s} 页面耗时={(it.get('browser_duration_ms') or 0) / 1000:.0f}s  "
                        f"问: {str(it.get('prompt_preview') or '')[:30]!r}")
                print(line)
                if it.get("error_message"):
                    print(f"           ↳ {str(it.get('error_message'))[:140]}")
            last = items[0] if items else None
            if last and last.get("status") != "ok":
                ec = last.get("error_code")
                tips = {
                    "submit_failed": "文字打进去了但没发出去——典型就是问卷压着输入框/发送按钮。",
                    "no_output": "扩展以为发出去了，却一直没看到回答（多半也是问卷挡住、其实没发出去；或 Agent 真在排队）。",
                    "dom_changed": "输入框没找到——页面结构变了，或问卷/登录墙把输入框藏起来了。",
                    "browser_offline": "请求进行中扩展断线了（刷新了页面 / 重新加载了扩展）。",
                    "site_idle": "页面长时间没动静，扩展放弃了。",
                    "page_timeout": "超过最长等待时间。",
                    "cancelled": "被取消（弹窗 Quick test 关闭、面板 Cancel、或客户端断开）。",
                }
                if ec in tips:
                    advice.append(("B", f"上一条请求失败原因 `{ec}`：{tips[ec]}"))
        else:
            print(f"  （读不到历史：{code}，面板可能被关了 AAB_PANEL_ENABLED=0）")

    # ------------------------------------------------------------- 3. 页面
    h("3. Arena 页面此刻的状态（经 Tabbit 调试端口）")
    page = None
    t = None
    try:
        targets = bridge.list_targets(cdp)
        arena = [x for x in targets if x.get("type") == "page" and args.target in ((x.get("url") or "") + " " + (x.get("title") or ""))]
        tabbit = [x for x in targets if x.get("type") == "page" and "web.tabbit.ai" in (x.get("url") or "")]
        print(f"{OK} 调试端口可用：{len(targets)} 个目标；含 '{args.target}' 的页 {len(arena)} 个，Tabbit 对话页 {len(tabbit)} 个")
        if len(arena) > 1:
            advice.append(("B", f"开了不止一个含 '{args.target}' 的标签页——扩展和 clicker 可能盯着不同的页，只留一个。"))
        if not arena:
            advice.append(("A", "Tabbit 里没有打开的 arena.ai/agent 页面。"))
        else:
            t = bridge.Target(bridge.pick_target(targets, args.target))
            page = t.eval(JS_PAGE)
            raw["page"] = page
            print(f"  页面: {page['url']}\n  消息数: {page['messages']}（用户 {page['userMessages']}）  Agent 在跑(Stop 按钮): {page['stop']}"
                  f"  输入框: {page['input'] or '无'}  发送按钮: {page['send'] or '无'}  登录按钮: {page['login']}  篡改猴 amd 元素: {page['amd']}")
            print(f"  问卷: {'**在**' if page['keepWorking'] else '不在'}（“继续工作”可见 {page['keepWorking']} 个，标题{'可见' if page['surveyTitle'] else '不可见'}）")
            if page.get("lastText"):
                print(f"  最后一条消息末尾: {page['lastText'][-100:]!r}")
            if page["amd"]:
                advice.append(("B", "页面里有篡改猴脚本注入的 #amd-* 元素，请在篡改猴里对 arena.ai 禁用它。"))
            if page["login"] and not page["messages"]:
                advice.append(("A", "页面像是没登录（有登录按钮、没有消息）。"))
            if page["keepWorking"]:
                advice.append(("A", "问卷正挂在页面上，此刻任何发送都会失败。开着 `python survey_clicker.py --cdp "
                                    f"{cdp} --target {args.target}` 就会自动点掉；现在马上点：`python doctor.py --fix`。"))
            elif not page["input"]:
                advice.append(("A", "既没有问卷也没有输入框——页面可能还在加载、在登录墙、或 Arena 改版了。刷新页面后再诊断一次。"))
    except SystemExit as e:
        print(f"{BAD} {e}")
        advice.append(("A", f"连不上调试端口 {cdp}：Tabbit 必须用 --remote-debugging-port=9223 启动（完全退出后重开）。"))

    # ------------------------------------------------------------- 4. 补丁
    h("4. 扩展补丁")
    repo = Path(args.aab_repo)
    dist = repo / "dist" / "chrome"
    if not repo.exists():
        print(f"  （{repo} 不存在，跳过；用 --aab-repo 指到 ArenaAgentBridge 目录）")
    else:
        src_c = repo / "extensions" / "shared" / "content.js"
        dist_c = dist / "content.js"
        dist_cfg = dist / "config.js"
        env = repo / ".env"
        def has(p, needles):
            try:
                s = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return None
            return [n for n in needles if n in s]
        sm, dm = has(src_c, MARKERS), has(dist_c, MARKERS)
        cfg_ok = has(dist_cfg, ["继续工作"])
        env_ok = has(env, ["AAB_AGENT_WRAPPER", "AAB_SANITIZE_MODE=detect", "AAB_REQUEST_TIMEOUT"])
        print(f"  源码 content.js 补丁: {len(sm or [])}/3   dist\\chrome\\content.js 补丁: {len(dm or [])}/3   "
              f"dist 的 config.js 含“继续工作”: {'是' if cfg_ok else '否'}   .env: {len(env_ok or [])}/3")
        raw["patch"] = {"src": sm, "dist": dm, "cfg": cfg_ok, "env": env_ok}
        if dm is None:
            advice.append(("A", f"dist\\chrome 不存在——还没打包。运行 `python {HERE / 'aab' / 'patch_aab.py'} --repo "
                                f"{repo}`，然后到 chrome://extensions 加载 {dist}。"))
        elif len(dm) < 3 or not cfg_ok:
            advice.append(("A", f"dist\\chrome 里的补丁不全（content.js {len(dm)}/3，config.js {'有' if cfg_ok else '无'}中文选择器）。"
                                f"重跑 `python {HERE / 'aab' / 'patch_aab.py'} --repo {repo}` → chrome://extensions 点扩展的“重新加载” "
                                "→ 刷新 Arena 标签页 → 重启 python -m server。"))
        if status and connected:
            for c in status.get("browser", {}).get("clients", []):
                pass  # 扩展版本号是上游的，补丁不改它，这里无法从服务器侧判断
    # ------------------------------------------------------------- 动作
    if args.cancel and pending:
        h("动作：取消卡住的请求")
        code, r = post(f"{args.server}/admin/api/browser/cancel", {"reason": "doctor.py --cancel"})
        print(f"  {code} {json.dumps(r, ensure_ascii=False)[:200]}")
        if code == 200 and isinstance(r, dict) and r.get("cancelled"):
            advice = [a for a in advice if "Send 置灰就是因为它" not in a[1]]
            advice.append(("B", f"已取消 {r.get('cancelled')} 条卡住的请求；Playground 的 Send 应该恢复了（页面不刷新也行）。"))
    if args.fix and page and page.get("keepWorking") and t is not None:
        h("动作：点掉问卷")
        ns = argparse.Namespace(strategies=sc.DEFAULT_STRATEGIES.split(","))
        info = sc.find(t, args.text, args.title)
        if info:
            print(sc.describe(info))
            ladder = sc.Ladder(t, ns, info)
            while not ladder.exhausted():
                ladder.step()
                time.sleep(1.5)
                info = sc.find(t, args.text, args.title)
                if not info:
                    comp = t.eval(sc.JS_COMPOSER) or {}
                    print(f"  {DONE} 问卷已消失（第 {ladder.pos} 步后）（输入框{'可见' if comp.get('input') else '不可见'}，"
                          f"发送按钮{'可见' if comp.get('send') else '不可见'}）")
                    advice = [a for a in advice if "问卷正挂在页面上" not in a[1]]
                    break
                ladder.info = info
            else:
                print("  {BAD} 所有招式都试过了问卷仍在——把上面“层级/卡片/HTML”几行发给 AI。")
    if t is not None:
        try:
            t.close()
        except Exception:
            pass

    # ------------------------------------------------------------- 结论
    h("结论 / 下一步")
    if not advice:
        print("  一切正常：服务器在、扩展连着、没有卡住的请求、页面上没有问卷。可以直接发。")
    else:
        advice.sort(key=lambda a: a[0])
        for i, (lvl, text) in enumerate(advice, 1):
            print(f"  {i}. [{'必须' if lvl == 'A' else '建议'}] {text}")
    if args.out:
        raw["advice"] = [a[1] for a in advice]
        Path(args.out).write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n原始数据已写入 {args.out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已停止。")
