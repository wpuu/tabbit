#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
survey_clicker.py — 盯着 Arena 页面，"此任务成功了吗? 是 / 否 / 继续工作" 问卷一出现，
就通过 Tabbit 的调试端口（CDP）把它点掉（默认点"继续工作"）。

为什么需要它：ArenaAgentBridge 扩展是在页面里用 JS 合成事件去点按钮，Arena 这个问卷有时点不动
（或问卷来得太晚被错过）。CDP 发出的是浏览器级别的输入事件（isTrusted=true），和你亲手点一样，页面区分不了。

v0.2 起是一个"阶梯"，一招不灵换下一招，每一步都打印结果：
    click   真实鼠标点击（按钮中心 → 文字中心 → 再按钮中心）
    key     让按钮获得焦点，发真实键盘 Enter / Space
    react   直接调用按钮上 React 挂的 onClick/onPointerDown 等处理函数
    escape  真实键盘 Esc（问卷右上角本来就有 Esc 提示）
    close   点问卷卡片右上角的 × 关闭按钮
问卷消失后再看一眼输入框、发送按钮是否回来了。

用法（单独开一个窗口一直跑着即可）：
    python survey_clicker.py --cdp http://127.0.0.1:9223 --target arena.ai/agent
    python survey_clicker.py ... --once            # 点掉一次就退出（手动救急）
    python survey_clicker.py ... --dry-run         # 只报告找到了什么、坐标在哪，不点
    python survey_clicker.py ... --strategies escape,click   # 自定义顺序（逗号分隔）
    python survey_clicker.py ... --text "Keep working" --title "Was this task successful"   # 英文界面

参数：
    --delay     问卷必须连续出现这么多秒才动手（默认 5）。留给扩展先把"这一轮结束"记下来，别抢在它前面。
    --interval  轮询间隔秒（默认 2）
    --title     问卷标题里的字（用来定位卡片和它的 × 按钮，默认 此任务成功了吗）
    --bring-to-front  动手前把 Arena 标签页切到前台（默认不切，免得打断 Tabbit 那个标签页）
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge  # noqa: E402

VERSION = "0.2"
DEFAULT_STRATEGIES = "click,key,react,escape,close"
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# 找到"继续工作"这个可点元素：先找文字以它开头、且文字很短（叶子级）的可见元素，取最内层，再向上找可点击的祖先。
# 找到的元素存到 window.__tabSurvey，后面的招式直接用。
JS_FIND = r"""
(() => {
  const want = %s, title = %s;
  const rectOf = (e) => {
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const cs = getComputedStyle(e);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0 || cs.pointerEvents === 'none') return null;
    return r;
  };
  const txt = (e) => (e.textContent || '').replace(/\s+/g, ' ').trim();
  const CLICKABLE = 'button, [role="button"], [role="option"], [role="menuitem"], [role="radio"], li, a, [tabindex]';
  let cands = [...document.querySelectorAll('button, [role="button"], [role="option"], [role="menuitem"], [role="radio"], li, a, span, div, p, label')]
    .filter((e) => txt(e).startsWith(want) && txt(e).length <= want.length + 12 && rectOf(e));
  if (!cands.length) { window.__tabSurvey = null; return null; }
  cands = cands.filter((e) => !cands.some((o) => o !== e && e.contains(o)));   // 只留最内层
  const inner = cands[cands.length - 1];
  const el = inner.closest(CLICKABLE) || inner;
  try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_) {}
  const r = rectOf(el) || rectOf(inner);
  if (!r) { window.__tabSurvey = null; return null; }
  const ri = rectOf(inner) || r;
  // 卡片：最近的、文字里含标题的祖先（最多向上 8 层）；找不到就用祖父
  let card = null;
  for (let n = el.parentElement, i = 0; n && i < 8; n = n.parentElement, i++) {
    if (title && txt(n).includes(title)) { card = n; break; }
  }
  if (!card) card = (el.parentElement && el.parentElement.parentElement) || el.parentElement || el;
  // 卡片里的关闭按钮：图标按钮（没文字 / × / aria-label 像 close）
  let closeBtn = null;
  for (const b of card.querySelectorAll('button, [role="button"]')) {
    if (b === el || el.contains(b) || !rectOf(b)) continue;
    const t = txt(b), al = ((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();
    if (/close|dismiss|关闭|skip|跳过/.test(al) || t === '' || /^(×|✕|✖|x|esc ×|esc)$/i.test(t)) { closeBtn = b; break; }
  }
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const top = document.elementFromPoint(cx, cy);
  const covered = !(top && (top === el || el.contains(top) || top.contains(el)));
  const chain = [];
  for (let n = el, i = 0; n && i < 6; n = n.parentElement, i++) {
    chain.push(n.tagName.toLowerCase() + (n.getAttribute('role') ? '[' + n.getAttribute('role') + ']' : '') + (n.getAttribute('data-state') ? '{' + n.getAttribute('data-state') + '}' : ''));
  }
  window.__tabSurvey = { el, inner, card, closeBtn };
  const cr = closeBtn ? closeBtn.getBoundingClientRect() : null;
  return { x: cx, y: cy, w: r.width, h: r.height, tx: ri.left + ri.width / 2, ty: ri.top + ri.height / 2,
           tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), text: txt(el).slice(0, 40),
           covered, topTag: top ? top.tagName.toLowerCase() : null, chain: chain.join(' < '), html: el.outerHTML.slice(0, 240),
           inViewport: cy >= 0 && cy <= innerHeight && cx >= 0 && cx <= innerWidth,
           cardTag: card.tagName.toLowerCase() + '.' + String(card.className || '').split(/\s+/).slice(0, 3).join('.'),
           close: cr ? { x: cr.left + cr.width / 2, y: cr.top + cr.height / 2, html: closeBtn.outerHTML.slice(0, 120) } : null };
})()
"""

JS_FOCUS = r"""
(() => { const s = window.__tabSurvey; if (!s || !s.el || !s.el.isConnected) return false;
  try { s.el.focus({ preventScroll: true }); } catch (_) { s.el.focus(); }
  return document.activeElement === s.el; })()
"""

# 直接调用 React 挂在元素（或其祖先）上的处理函数
JS_REACT = r"""
(() => {
  const s = window.__tabSurvey; if (!s || !s.el || !s.el.isConnected) return { ok: false, why: 'no element' };
  const el = s.el;
  const names = ['onClick', 'onPointerUp', 'onMouseUp', 'onPointerDown', 'onMouseDown', 'onSelect'];
  let anyProps = false;
  for (let n = el, depth = 0; n && depth < 5; n = n.parentElement, depth++) {
    const key = Object.keys(n).find((k) => k.startsWith('__reactProps$'));
    if (!key) continue;
    anyProps = true;
    const props = n[key];
    for (const name of names) {
      if (typeof props[name] !== 'function') continue;
      const type = name.slice(2).toLowerCase();
      const native = new MouseEvent(type, { bubbles: true, cancelable: true, view: window });
      const ev = { type, target: el, currentTarget: n, nativeEvent: native, isTrusted: true, button: 0, buttons: 0,
                   detail: 1, clientX: 0, clientY: 0, pointerType: 'mouse', defaultPrevented: false,
                   preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, persist() {},
                   isDefaultPrevented() { return this.defaultPrevented; }, isPropagationStopped() { return false; } };
      try { props[name](ev); return { ok: true, called: name + ' @ ' + n.tagName.toLowerCase() + (depth ? ' (向上 ' + depth + ' 层)' : '') }; }
      catch (e) { return { ok: false, why: name + ' 抛错: ' + (e && e.message) }; }
    }
  }
  return { ok: false, why: anyProps ? '有 React 属性但没有点击处理函数' : '元素上没有 React 属性（不是 React 页面？）' };
})()
"""

JS_COMPOSER = r"""
(() => {
  const vis = (e) => { if (!e) return false; const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const ta = [...document.querySelectorAll('textarea, [contenteditable="true"]')].find(vis);
  const send = document.querySelector('button[aria-label="Send message"], button[aria-label="发送"], button[type="submit"]');
  return { input: Boolean(ta), send: Boolean(send && vis(send)), sendDisabled: Boolean(send && send.disabled) };
})()
"""


def now():
    return time.strftime("%H:%M:%S")


def say(msg):
    print(f"[{now()}] {msg}", flush=True)


def find(t: bridge.Target, text: str, title: str):
    return t.eval(JS_FIND % (json.dumps(text), json.dumps(title or "")))


def real_click(t: bridge.Target, x: float, y: float):
    t.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    time.sleep(0.08)
    t.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", buttons=1, clickCount=1)
    time.sleep(0.08)
    t.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", buttons=0, clickCount=1)


def press_key(t: bridge.Target, key: str, code: str, vk: int, text: str = None):
    down = dict(type="keyDown", key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
    if text is not None:
        down.update(text=text, unmodifiedText=text)
    t.call("Input.dispatchKeyEvent", **down)
    time.sleep(0.05)
    t.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)


def press_escape(t: bridge.Target):
    press_key(t, "Escape", "Escape", 27)


def attach(cdp: str, match: str) -> bridge.Target:
    info = bridge.pick_target(bridge.list_targets(cdp), match)
    say(f"盯着: [{info.get('type')}] {info.get('title')!r} {info.get('url')}")
    return bridge.Target(info)


class Ladder:
    """一次问卷的处理过程：按 strategies 顺序逐招尝试，每招之间重新定位元素。"""

    def __init__(self, t: bridge.Target, args, info):
        self.t, self.args, self.info = t, args, info
        self.steps = []          # 展开后的动作列表
        for s in args.strategies:
            if s == "click":
                self.steps += [("click", "center"), ("click", "text"), ("click", "center")]
            elif s == "key":
                self.steps += [("key", "Enter"), ("key", "Space")]
            elif s in ("react", "escape", "close"):
                self.steps.append((s, None))
            else:
                say(f"未知策略 '{s}'，忽略")
        self.pos = 0

    def exhausted(self):
        return self.pos >= len(self.steps)

    def step(self):
        kind, var = self.steps[self.pos]
        self.pos += 1
        t, info = self.t, self.info
        no = self.pos
        try:
            if kind == "click":
                x, y = (info["tx"], info["ty"]) if var == "text" else (info["x"], info["y"])
                say(f"#{no} 真实点击 {'文字中心' if var == 'text' else '按钮中心'} @({x:.0f},{y:.0f})")
                real_click(t, x, y)
            elif kind == "key":
                focused = t.eval(JS_FOCUS)
                say(f"#{no} 聚焦按钮（{'成功' if focused else '失败'}）后发真实键盘 {var}")
                if var == "Enter":
                    press_key(t, "Enter", "Enter", 13, "\r")
                else:
                    press_key(t, " ", "Space", 32, " ")
            elif kind == "react":
                r = t.eval(JS_REACT) or {}
                say(f"#{no} 直接调用 React 处理函数：{'已调用 ' + r.get('called', '') if r.get('ok') else '不行（' + str(r.get('why')) + '）'}")
            elif kind == "escape":
                say(f"#{no} 真实键盘 Esc")
                press_escape(t)
            elif kind == "close":
                c = info.get("close")
                if not c:
                    say(f"#{no} 卡片里没找到 × 关闭按钮，跳过")
                    return
                say(f"#{no} 点卡片的 × 关闭按钮 @({c['x']:.0f},{c['y']:.0f})  {c['html']}")
                real_click(t, c["x"], c["y"])
        except RuntimeError as e:
            say(f"#{no} 执行失败: {e}")


def describe(info):
    return (f"看到 '{info['text']}'：<{info['tag']}{' role=' + info['role'] if info['role'] else ''}> "
            f"@({info['x']:.0f},{info['y']:.0f}) {info['w']:.0f}x{info['h']:.0f} covered={info['covered']}"
            f"{'(' + str(info['topTag']) + ')' if info['covered'] else ''} 视口内={info['inViewport']}\n"
            f"    层级: {info['chain']}\n    卡片: {info['cardTag']}   ×按钮: {'有' if info.get('close') else '无'}\n"
            f"    HTML: {info['html']}")


def main():
    ap = argparse.ArgumentParser(description="用 CDP 真实输入把 Arena 的“此任务成功了吗?”问卷点掉")
    ap.add_argument("--version", action="version", version=f"survey_clicker.py {VERSION} (bridge.py {bridge.VERSION})")
    ap.add_argument("--cdp", default=None)
    ap.add_argument("--target", default="arena.ai/agent", help="URL/标题里包含的子串（多个 Arena 标签页时写得更具体）")
    ap.add_argument("--text", default="继续工作", help="要点的选项文字（英文界面填 Keep working）")
    ap.add_argument("--title", default="此任务成功了吗", help="问卷标题里的字（英文界面填 Was this task successful）")
    ap.add_argument("--delay", type=float, default=5.0, help="问卷连续出现多少秒后才动手")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--strategies", default=DEFAULT_STRATEGIES, help=f"逗号分隔，默认 {DEFAULT_STRATEGIES}")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bring-to-front", action="store_true")
    ap.add_argument("--no-escape", dest="no_escape", action="store_true", help="等同于从 strategies 里去掉 escape")
    args = ap.parse_args()
    args.strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    if args.no_escape:
        args.strategies = [s for s in args.strategies if s != "escape"]
    cfg = {}
    try:  # 同目录 config.json 的 cdp_url 作为默认（你的 Tabbit 是 9223）
        cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    cdp = bridge.resolve_cdp(args, cfg)
    print(f"survey_clicker.py v{VERSION} · cdp={cdp} · target~'{args.target}' · text='{args.text}' · delay={args.delay}s "
          f"· 顺序={','.join(args.strategies)}", flush=True)

    t = None
    first_seen = None
    ladder = None
    solved = 0
    while True:
        try:
            if t is None:
                t = attach(cdp, args.target)
            info = find(t, args.text, args.title)
        except SystemExit as e:                       # 连不上 / 找不到标签页
            say(f"{e}；{int(args.interval * 5)}s 后重试")
            t = None
            time.sleep(args.interval * 5)
            continue
        except Exception as e:                        # 页面刷新导致连接失效等
            say(f"连接异常（{e.__class__.__name__}），重连…")
            try:
                t.close()
            except Exception:
                pass
            t = None
            time.sleep(args.interval)
            continue

        if not info:
            if first_seen is not None:
                how = f"第 {ladder.pos} 步后" if ladder and ladder.pos else "没动手它自己就"
                try:
                    comp = t.eval(JS_COMPOSER) or {}
                    tail = f"（输入框{'可见' if comp.get('input') else '不可见'}，发送按钮{'可见' if comp.get('send') else '不可见'}）"
                except Exception:
                    tail = ""
                say(f"✔ 问卷已消失（{how}）{tail}")
                solved += 1
                if args.once and ladder and ladder.pos:
                    return 0
            first_seen, ladder = None, None
            time.sleep(args.interval)
            continue

        if first_seen is None:
            first_seen = time.time()
            say(describe(info))
        if args.dry_run:
            time.sleep(args.interval)
            continue
        if time.time() - first_seen < args.delay:
            time.sleep(min(args.interval, 1.0))
            continue

        if ladder is None:
            ladder = Ladder(t, args, info)
            if args.bring_to_front:
                try:
                    t.call("Page.bringToFront")
                except Exception:
                    pass
        else:
            ladder.info = info                        # 每一步前重新定位过了
        if ladder.exhausted():
            say(f"所有招式都试过问卷仍在，{args.interval * 5:.0f}s 后从头再来（把上面“层级/卡片/HTML”几行发给我）")
            ladder = None
            first_seen = time.time()
            time.sleep(args.interval * 5)
            continue
        ladder.step()
        time.sleep(1.5)                               # 给页面一点反应时间，下一轮循环重新 find 判断


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已停止。")
