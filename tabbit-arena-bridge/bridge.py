#!/usr/bin/env python3
"""
tabbit-arena-bridge / bridge.py
================================
用 Chrome DevTools Protocol (CDP) 同时驱动「Arena Agent 网页」和「Tabbit 对话（侧边栏 / 新标签页）」，
让两边自动一问一答，直到触发"需要人工干预"的条件。

为什么用 CDP 而不是油猴：
  Tabbit 的侧边栏对话不是普通 http(s) 网页，用户脚本/篡改猴注入不进去；而 --remote-debugging-port
  暴露的 CDP 能看到浏览器里 **所有** WebContents（标签页、侧边栏、内部页面），并可对它们执行 JS、注入输入。

用法：
  python bridge.py list                                   # 列出所有可控目标，找出 Arena 和 Tabbit 对话各自的条目
  python bridge.py probe --target arena.ai                # 打印候选输入框 / 按钮 / 选择器（辅助填 config）
  python bridge.py probe --target tabbit --text PROBE-7391 # 按文字定位消息容器，推断消息选择器
  python bridge.py dump --target arena.ai --selector main --out arena.html   # 导出精简 HTML 供分析
  python bridge.py run --config config.json --dry-run     # 只观察、不发送，验证"回复完成"的判定
  python bridge.py run --config config.json               # 正式循环

依赖：pip install requests websocket-client
"""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import websocket  # websocket-client

VERSION = "0.7"   # 2026-09-29：选择器按逗号顺序取第一个命中的（pick）；force_native_input 不再重复插入；识别 data-send-blocked
INBOX = Path("human_inbox.txt")
FLAG = Path("NEEDS_HUMAN.flag")

# --------------------------------------------------------------------------- CDP 基础

DEFAULT_CDP = "http://127.0.0.1:9222"


def list_targets(cdp_url: str):
    try:
        r = requests.get(cdp_url.rstrip("/") + "/json/list", timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise SystemExit(f"连不上 {cdp_url} （{e.__class__.__name__}）\n"
                         f"  → 请确认浏览器是用 --remote-debugging-port=<端口> 启动的，并且端口号和这里一致；\n"
                         f"  → 在浏览器里打开 {cdp_url}/json/version 应能看到 JSON；\n"
                         f"  → 若打不开，多半是浏览器启动时已有实例在运行（参数被忽略），请完全退出后再启动；\n"
                         f"  → Chromium 136+ 还要求同时加一个非默认的 --user-data-dir。")


class Target:
    """一个 CDP 目标（标签页 / 侧边栏 / 内部页面）。"""

    def __init__(self, info: dict):
        self.info = info
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"], suppress_origin=True, timeout=60)
        self._id = 0
        for dom in ("Runtime.enable", "Page.enable"):
            try:
                self.call(dom)
            except RuntimeError:
                pass  # iframe / browser_ui 类目标可能不支持 Page 域，不影响 Runtime.evaluate

    def call(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 失败: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expression: str):
        res = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in res:
            raise RuntimeError("页面 JS 报错: " + json.dumps(res["exceptionDetails"], ensure_ascii=False)[:500])
        return res.get("result", {}).get("value")

    def insert_text_native(self, text: str):
        """像真人打字一样把文本送进当前聚焦的输入框（对 contenteditable / 富文本编辑器最可靠）。"""
        self.call("Input.insertText", text=text)

    def press_enter(self):
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key="Enter", code="Enter",
                      windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text="\r" if t == "keyDown" else "")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


TYPE_PRIORITY = {"page": 0, "browser_ui": 1, "webview": 2, "other": 3, "iframe": 4}


def pick_target(targets, match: str, exclude_devtools=True):
    cands = []
    for t in targets:
        hay = (t.get("url", "") + " " + t.get("title", "")).lower()
        if exclude_devtools and t.get("url", "").startswith("devtools://"):
            continue
        if t.get("type") in ("service_worker", "background_page", "shared_worker", "worker"):
            continue
        if match.lower() in hay:
            cands.append(t)
    cands.sort(key=lambda t: TYPE_PRIORITY.get(t.get("type"), 9))
    if not cands:
        raise SystemExit(f"找不到包含 '{match}' 的目标，请先运行 `python bridge.py list` 核对。")
    if len(cands) > 1:
        print(f"[warn] 有 {len(cands)} 个目标匹配 '{match}'，使用第一个；可把 target_match 写得更具体：")
        for c in cands:
            print(f"       - [{c.get('type')}] {c.get('title')!r}  {c.get('url')}")
    return cands[0]


# --------------------------------------------------------------------------- 注入到页面里的 JS 工具

JS_HELPERS = r"""
(() => {
  if (window.__bridge) return true;
  const q = (sel, root=document) => sel ? Array.from(root.querySelectorAll(sel)) : [];
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  // 输入框/按钮：选择器用逗号分成几段，按顺序找，第一段有可见命中就用它（取最后一个可见的）；这样可以写“优先级列表”
  const pick = (sel) => {
    if (!sel) return null;
    for (const part of sel.split(',').map(s => s.trim()).filter(Boolean)) {
      let els = [];
      try { els = q(part).filter(visible); } catch (e) { continue; }
      if (els.length) return els[els.length - 1];
    }
    return null;
  };
  window.__bridge = {
    pick,
    count(sel) { return q(sel).length; },
    lastText(sel) { const a = q(sel); return a.length ? (a[a.length-1].innerText || a[a.length-1].textContent || '').trim() : ''; },
    busy(sel) { return q(sel).some(visible); },
    humanWidget(msgSel, widgetSel) {
      if (!widgetSel) return false;
      const a = q(msgSel); const root = a.length ? a[a.length-1] : document;
      return q(widgetSel, root).some(visible);
    },
    clearInput(sel) {
      const el = pick(sel);
      if (!el) return 'NO_INPUT';
      el.focus();
      if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, '');
        el.dispatchEvent(new Event('input', {bubbles: true}));
        return 'OK';
      }
      document.execCommand('selectAll', false, null);
      document.execCommand('delete', false, null);
      return 'OK';
    },
    setInput(sel, text) {
      const el = pick(sel);
      if (!el) return 'NO_INPUT';
      el.focus();
      if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return 'OK_VALUE';
      }
      // contenteditable / 富文本：先全选清空，再用 execCommand 插入（会触发 beforeinput/input，React/ProseMirror/Slate 都认）
      document.execCommand('selectAll', false, null);
      document.execCommand('delete', false, null);
      const ok = document.execCommand('insertText', false, text);
      return ok ? 'OK_EXEC' : 'NEED_NATIVE';
    },
    focusInput(sel) { const el = pick(sel); if (!el) return false; el.focus(); return true; },
    inputText(sel) { const el = pick(sel); return el ? (el.value !== undefined ? el.value : el.innerText) : null; },
    pressEnterJS(sel) {
      const el = pick(sel); if (!el) return 'NO_INPUT';
      el.focus();
      for (const type of ['keydown', 'keypress', 'keyup']) {
        el.dispatchEvent(new KeyboardEvent(type, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
      }
      const form = el.closest('form'); if (form && typeof form.requestSubmit === 'function') { try { form.requestSubmit(); } catch (e) {} }
      return 'OK';
    },
    click(sel) {
      const el = pick(sel);
      if (!el) return 'NO_BUTTON';
      if (el.disabled || el.getAttribute('aria-disabled') === 'true' || el.getAttribute('data-send-blocked') === 'true') return 'DISABLED';
      el.click(); return 'CLICKED';
    },
  };
  return true;
})()
"""

JS_PROBE = r"""
((TEXT) => {
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const cssPath = el => {
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
      let s = el.tagName.toLowerCase();
      if (el.id) { parts.unshift(s + '#' + CSS.escape(el.id)); break; }
      const dt = el.getAttribute('data-testid'); if (dt) { parts.unshift(`${s}[data-testid="${dt}"]`); break; }
      const cls = Array.from(el.classList).filter(c => !/^\d|^css-|^sc-/.test(c)).slice(0, 2);
      if (cls.length) s += '.' + cls.map(c => CSS.escape(c)).join('.');
      else if (el.parentElement) { const sib = Array.from(el.parentElement.children).filter(x => x.tagName === el.tagName); if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(el)+1})`; }
      parts.unshift(s); el = el.parentElement;
    }
    return parts.join(' > ');
  };
  const desc = el => ({
    tag: el.tagName.toLowerCase(), visible: visible(el), id: el.id || undefined,
    classes: Array.from(el.classList).slice(0, 4).join(' ') || undefined,
    aria: el.getAttribute('aria-label') || undefined, placeholder: el.getAttribute('placeholder') || undefined,
    testid: el.getAttribute('data-testid') || undefined, text: (el.innerText || '').trim().slice(0, 40) || undefined,
    disabled: el.disabled || el.getAttribute('aria-disabled') === 'true' || undefined,
    selector: cssPath(el),
  });
  const out = { url: location.href, title: document.title, inputs: [], buttons: [], commonSelectorCounts: {}, textMatches: [] };
  for (const g of ['[data-message-author-role="assistant"]', '[data-role="assistant"]', '[data-author="assistant"]', '[class*="assistant"]',
                   '[data-testid*="assistant"]', '[data-testid*="message"]', '[class*="message"]', '[class*="markdown"]', '[class*="prose"]', 'article', '[role="article"]']) {
    try { const n = document.querySelectorAll(g).length; if (n) out.commonSelectorCounts[g] = n; } catch (e) {}
  }
  document.querySelectorAll('textarea, input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]), [contenteditable]:not([contenteditable="false"]), [role="textbox"]').forEach(el => out.inputs.push(desc(el)));
  out.visibleButtons = Array.from(document.querySelectorAll('button, [role="button"], a[href]')).filter(visible).slice(0, 120).map(el => {
    const d = desc(el); delete d.selector; delete d.visible; return d;
  });
  document.querySelectorAll('button, [role="button"]').forEach(el => {
    const t = ((el.innerText||'') + ' ' + (el.getAttribute('aria-label')||'') + ' ' + (el.title||'') + ' ' + (el.getAttribute('data-testid')||'')).toLowerCase();
    if (el.type === 'submit' || /send|发送|提交|submit|stop|停止|keep working|继续/.test(t)) out.buttons.push(desc(el));
  });
  if (TEXT) {
    const all = Array.from(document.querySelectorAll('body *')).filter(el => !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(el.tagName)).filter(el => el.children.length === 0 || Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.includes(TEXT)));
    const hits = all.filter(el => (el.textContent || '').includes(TEXT)).slice(-3);
    hits.forEach(el => {
      const chain = []; let cur = el;
      for (let i = 0; i < 8 && cur && cur !== document.body; i++) {
        const sibSame = cur.parentElement ? Array.from(cur.parentElement.children).filter(x => x.tagName === cur.tagName && x.className === cur.className).length : 1;
        const selPath = cssPath(cur); let matches = -1; try { matches = document.querySelectorAll(selPath).length; } catch (e) {}
        chain.push({ level: i, selector: selPath, matchesOnPage: matches, similarSiblings: sibSame, chars: (cur.innerText||'').length,
                     attrs: Array.from(cur.attributes).filter(a => /^data-|^role$|^class$|^id$/.test(a.name)).map(a => a.name + '=' + a.value.slice(0, 60)) });
        cur = cur.parentElement;
      }
      out.textMatches.push(chain);
    });
  }
  return out;
})
"""


JS_DUMP = r"""
((SEL, MAXTEXT) => {
  const els = Array.from(document.querySelectorAll(SEL)).slice(0, 20);
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const clean = (node) => {
    const hiddenPaths = new Set();
    node.querySelectorAll('*').forEach(n => { if (!visible(n)) n.setAttribute('data-hidden', '1'); });
    const c = node.cloneNode(true);
    node.querySelectorAll('[data-hidden]').forEach(n => n.removeAttribute('data-hidden'));
    c.querySelectorAll('svg, script, style, noscript, template, link, meta, canvas, video, audio, source, iframe').forEach(n => n.remove());
    c.querySelectorAll('img').forEach(n => { n.removeAttribute('src'); n.removeAttribute('srcset'); });
    c.querySelectorAll('*').forEach(n => {
      for (const a of Array.from(n.attributes)) {
        if (a.name === 'style' || a.name.startsWith('on')) n.removeAttribute(a.name);
        else if (a.value.length > 200 && !/^(class|data-|aria-|id$|role$|placeholder$|type$|name$)/.test(a.name)) n.setAttribute(a.name, a.value.slice(0, 80) + '…');
      }
    });
    const walker = document.createTreeWalker(c, NodeFilter.SHOW_TEXT); const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(t => { if (t.textContent.length > MAXTEXT) t.textContent = t.textContent.slice(0, MAXTEXT) + '…[截断]'; });
    return c.outerHTML.replace(/\s+/g, ' ').replace(/> </g, '>\n<');
  };
  return { url: location.href, title: document.title, count: document.querySelectorAll(SEL).length, html: els.map(clean) };
})
"""


def ensure_helpers(t: Target):
    t.eval(JS_HELPERS)


# --------------------------------------------------------------------------- 一侧（Arena 或 Tabbit）

class Side:
    def __init__(self, name: str, cfg: dict, target: Target):
        self.name, self.cfg, self.t = name, cfg, target
        ensure_helpers(target)

    def snapshot(self):
        sel = self.cfg["assistant_messages"]
        return self.t.eval(f"[__bridge.count({json.dumps(sel)}), __bridge.lastText({json.dumps(sel)})]")

    def busy(self):
        return bool(self.cfg.get("busy")) and self.t.eval(f"__bridge.busy({json.dumps(self.cfg['busy'])})")

    def human_widget(self):
        return self.t.eval(f"__bridge.humanWidget({json.dumps(self.cfg['assistant_messages'])}, {json.dumps(self.cfg.get('human_needed', ''))})")

    def wait_for_reply(self, prev_count: int, prev_text: str) -> str:
        """等到出现新的 assistant 消息，且不忙、文本连续 stable_seconds 不变。"""
        poll = float(self.cfg.get("poll_seconds", 3))
        stable_need = float(self.cfg.get("stable_seconds", 15))
        deadline = time.time() + float(self.cfg.get("max_wait_seconds", 1800))
        last_text, stable_since = None, None
        while time.time() < deadline:
            ensure_helpers(self.t)  # 页面若刷新，重新注入
            count, text = self.snapshot()
            new_reply = count > prev_count or (count == prev_count and text and text != prev_text)
            if new_reply and not self.busy():
                if text != last_text:
                    last_text, stable_since = text, time.time()
                elif time.time() - stable_since >= stable_need:
                    return text
            else:
                last_text, stable_since = None, None
            time.sleep(poll)
        raise TimeoutError(f"{self.name} 超过 {self.cfg.get('max_wait_seconds')}s 仍未回复完成")

    def send(self, text: str) -> None:
        inp, btn = self.cfg["input"], self.cfg.get("send_button")
        method = self.cfg.get("send_method", "click")
        if self.cfg.get("force_native_input"):
            # 富文本编辑器（Tiptap/ProseMirror 等）：先清空，再像真人打字一样用 CDP 插入，避免 execCommand + 原生各插一次
            r = self.t.eval(f"__bridge.clearInput({json.dumps(inp)})")
            if r == "NO_INPUT":
                raise RuntimeError(f"{self.name}: 找不到输入框 {inp!r}")
            self.t.insert_text_native(text)
        else:
            r = self.t.eval(f"__bridge.setInput({json.dumps(inp)}, {json.dumps(text)})")
            if r == "NO_INPUT":
                raise RuntimeError(f"{self.name}: 找不到输入框 {inp!r}")
            if r == "NEED_NATIVE":
                self.t.eval(f"__bridge.focusInput({json.dumps(inp)})")
                self.t.insert_text_native(text)
        time.sleep(0.8)
        if method in ("enter", "enter_js"):
            self.t.eval(f"__bridge.focusInput({json.dumps(inp)})")
            sent = False
            if method == "enter":
                try:
                    self.t.press_enter(); sent = True
                except RuntimeError as e:
                    print(f"    [warn] CDP 回车失败（{e}），改用 JS 合成键盘事件", file=sys.stderr)
            if not sent:
                self.t.eval(f"__bridge.pressEnterJS({json.dumps(inp)})")
        else:
            r2 = self.t.eval(f"__bridge.click({json.dumps(btn)})")
            if r2 != "CLICKED":
                raise RuntimeError(f"{self.name}: 发送按钮状态 {r2}（选择器 {btn!r}）")
        # 校验：输入框应当被清空
        time.sleep(1.5)
        left = self.t.eval(f"__bridge.inputText({json.dumps(inp)})") or ""
        if left.strip() and left.strip() == text.strip():
            raise RuntimeError(f"{self.name}: 发送后输入框未清空，可能没发出去（试试 send_method 改成 enter/click，或 force_native_input=true）")


# --------------------------------------------------------------------------- 循环控制

def hit_stop_token(text: str, tokens) -> str:
    """停机标记只在"某一行的行首"或"最后一行"出现时才算数，避免模型在讨论中复述规则时误触发。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    for tk in tokens:
        if tk in last or any(ln.startswith(tk) for ln in lines):
            return tk
    return ""


def similarity(a: str, b: str) -> float:
    def grams(s):
        s = re.sub(r"\s+", " ", s.strip().lower())
        return {s[i:i + 3] for i in range(max(0, len(s) - 2))}
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def notify(title: str, body: str, webhook: str = ""):
    print("\n" + "=" * 72 + f"\n🛑 {title}\n{body}\n" + "=" * 72, flush=True)
    FLAG.write_text(f"{title}\n{body}", encoding="utf-8")
    sys.stdout.write("\a"); sys.stdout.flush()
    body = body.replace("'", "’").replace('"', "”")
    title = title.replace("'", "’").replace('"', "”")
    try:
        if platform.system() == "Windows":
            import ctypes
            ctypes.windll.user32.MessageBeep(0x30)
            subprocess.Popen(["powershell", "-NoProfile", "-Command",
                              f"[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
                              f"[System.Windows.Forms.MessageBox]::Show('{body[:200]}','{title}')"])
        elif platform.system() == "Darwin":
            subprocess.Popen(["osascript", "-e", f'display notification "{body[:200]}" with title "{title}"'])
    except Exception:
        pass
    if webhook:
        try:
            requests.post(webhook, json={"text": f"{title}\n{body}"}, timeout=10)
        except requests.RequestException:
            pass


def log(path: str, entry: dict):
    entry["ts"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def resolve_cdp(args, cfg=None):
    """优先级：命令行 --cdp > 环境变量 CDP_URL > config.json 的 cdp_url > 默认 9222"""
    return args.cdp or os.environ.get("CDP_URL") or (cfg or {}).get("cdp_url") or DEFAULT_CDP


def cmd_list(args):
    cdp = resolve_cdp(args)
    targets = list_targets(cdp)
    print(f"bridge.py v{VERSION} · 共 {len(targets)} 个目标（来自 {cdp}）：")
    for i, t in enumerate(targets):
        mark = ""
        u = (t.get("url") or "").lower()
        if "arena.ai" in u: mark = "   ← 可能是 Arena"
        elif "tabbit" in u and t.get("type") not in ("service_worker", "background_page"): mark = "   ← 可能是 Tabbit 对话"
        print(f"[{i:2d}] type={t.get('type'):<12} title={t.get('title')!r}{mark}\n      url={t.get('url')}")


def cmd_probe(args):
    t = Target(pick_target(list_targets(resolve_cdp(args)), args.target))
    res = t.eval(f"({JS_PROBE})({json.dumps(args.text or '')})")
    text = json.dumps(res, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n（已保存到 {args.out}，用记事本打开全选复制即可）")
    if args.text and not res.get("textMatches"):
        print(f"\n[提示] 页面上没找到文字 {args.text!r}：确认这条回复已经显示出来，且文字一字不差（含大小写/横线）。")
    t.close()


def cmd_dump(args):
    t = Target(pick_target(list_targets(resolve_cdp(args)), args.target))
    res = t.eval(f"({JS_DUMP})({json.dumps(args.selector)}, {int(args.max_text)})")
    parts = [f"<!-- dump of {res['url']} | title={res['title']} | selector={args.selector} | matches={res['count']} | {datetime.now().isoformat(timespec='seconds')} -->"]
    for i, h in enumerate(res["html"]):
        parts.append(f"\n<!-- ===== match #{i} ===== -->\n{h}")
    text = "\n".join(parts)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"已导出 {res['count']} 个匹配（最多前 20 个），{len(text)} 字符 → {args.out}")
    print("提示：文件里 data-hidden=\"1\" 表示导出时该元素不可见。")
    t.close()


def cmd_run(args):
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    loop = cfg["loop"]
    cdp = resolve_cdp(args, cfg)
    sides = {}
    for name in ("arena", "tabbit"):
        side_cdp = cfg[name].get("cdp_url", cdp)          # 两边可以在不同浏览器（不同端口）
        info = pick_target(list_targets(side_cdp), cfg[name]["target_match"])
        print(f"▶ {name}: [{info.get('type')}] {info.get('title')!r} {info.get('url')}  via {side_cdp}")
        sides[name] = Side(name, cfg[name], Target(info))

    order = ["arena", "tabbit"] if loop.get("first_speaker", "arena") == "arena" else ["tabbit", "arena"]
    speaker, listener = order
    snap = {n: sides[n].snapshot() for n in sides}
    if args.use_last:  # 首发方已经回复完了才启动脚本：把它当前最后一条当作第 1 轮
        c, _ = snap[speaker]
        snap[speaker] = (max(c - 1, 0), "")
    last_said = {"arena": "", "tabbit": ""}
    stop_tokens = loop.get("stop_tokens", ["[DONE]", "[NEED_HUMAN]"])
    transcript = loop.get("transcript", "transcript.jsonl")
    webhook = loop.get("notify_webhook", "")
    if FLAG.exists():
        FLAG.unlink()
    log(transcript, {"event": "start", "dry_run": args.dry_run})
    print(f"▶ 开始：先等待 {speaker} 的回复…（dry-run={args.dry_run}）")

    for n in range(1, int(loop.get("max_rounds", 30)) + 1):
        try:
            text = sides[speaker].wait_for_reply(*snap[speaker])
        except TimeoutError as e:
            notify("等待超时", str(e), webhook); return 2
        snap[speaker] = sides[speaker].snapshot()
        print(f"\n─── 第 {n} 轮 · {speaker} 说（{len(text)} 字）───\n{text[:800]}{'…' if len(text) > 800 else ''}\n")
        log(transcript, {"event": "turn", "n": n, "speaker": speaker, "text": text})

        hit = hit_stop_token(text, stop_tokens)
        if hit:
            notify(f"{speaker} 输出了 {hit}", text[-500:], webhook); return 0
        if sides[speaker].human_widget():
            notify(f"{speaker} 弹出了需要你选择/回答的组件", text[-500:], webhook); return 0
        sim = similarity(last_said[speaker], text)
        if last_said[speaker] and sim >= float(loop.get("repeat_threshold", 0.85)):
            notify(f"{speaker} 连续两轮高度重复（{sim:.2f}），疑似死循环", text[-300:], webhook); return 0
        last_said[speaker] = text

        # 人类插话
        human = INBOX.read_text(encoding="utf-8").strip() if INBOX.exists() else ""
        if human:
            INBOX.unlink()
            log(transcript, {"event": "human", "text": human})
        wrap = loop.get(f"wrap_to_{listener}", "{text}")
        outgoing = wrap.format(n=n, text=text) + (f"\n\n【人类插话】{human}" if human else "")

        if args.dry_run:
            print(f"[dry-run] 本应发送给 {listener}（{len(outgoing)} 字），跳过。按 Ctrl+C 结束。")
        else:
            snap[listener] = sides[listener].snapshot()   # 发送前记录，用于判断"新回复"
            try:
                sides[listener].send(outgoing)
            except RuntimeError as e:
                notify("发送失败", str(e), webhook); return 2
            log(transcript, {"event": "sent", "n": n, "to": listener, "chars": len(outgoing)})
        speaker, listener = listener, speaker
        time.sleep(float(loop.get("cooldown_seconds", 20)))

    notify("达到最大轮数", f"max_rounds={loop.get('max_rounds')}，看 {transcript} 决定是否继续", webhook)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Arena Agent ↔ Tabbit 对话 CDP 桥")
    ap.add_argument("--version", action="version", version=f"bridge.py {VERSION}")
    ap.add_argument("--cdp", default=None, help="远程调试地址，如 http://127.0.0.1:9223（优先级高于 config.json 与环境变量 CDP_URL）")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cdp", default=argparse.SUPPRESS, help="远程调试地址（也可放在子命令后面）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", parents=[common])
    p = sub.add_parser("probe", parents=[common]); p.add_argument("--target", required=True); p.add_argument("--text", default="")
    p.add_argument("--out", default="", help="把结果另存为 UTF-8 文件，例如 --out probe-arena.json")
    d = sub.add_parser("dump", parents=[common], help="导出页面精简 HTML（去掉 svg/script/style，长文本截断），用于人工/AI 分析结构")
    d.add_argument("--target", required=True); d.add_argument("--selector", default="main, body")
    d.add_argument("--out", default="dump.html"); d.add_argument("--max-text", default=300, type=int)
    r = sub.add_parser("run", parents=[common]); r.add_argument("--config", default="config.json"); r.add_argument("--dry-run", action="store_true")
    r.add_argument("--use-last", action="store_true", help="首发方已经回复完了：把它当前最后一条回复当作第 1 轮立即转发，而不是等新回复")
    args = ap.parse_args()
    try:
        return {"list": cmd_list, "probe": cmd_probe, "dump": cmd_dump, "run": cmd_run}[args.cmd](args)
    except KeyboardInterrupt:
        print("\n已手动停止。"); return 130


if __name__ == "__main__":
    sys.exit(main())
