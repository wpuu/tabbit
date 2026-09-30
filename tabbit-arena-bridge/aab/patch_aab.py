#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_aab.py — 给现成项目 ArenaAgentBridge（github.com/startify2647/ArenaAgentBridge）打几个补丁，
让它适合"Arena Agent ↔ 另一个 AI 长时间来回对话"的用法，然后重新打包扩展。

它改两处：
  1) extensions/shared/config.js
     - 中文界面：把"继续工作"按钮加进 keepWorking / 把"停止生成"加进 stopButton
       （原项目只认英文 "Keep working" / "Stop generating"，中文界面下答完一轮后输入框不会回来）
     - 把 survey 选择器清空：只用"继续工作"按钮作为"这一轮结束"的标志，避免把页面上普通的反馈组件误判为结束
     - 放宽所有等待阈值：Agent 一轮可能跑几十分钟、中间执行命令时页面几分钟不动，默认 3–45 秒的阈值会把半截回答当成结束
     - 关闭页面数据流钩子（capture.ENABLED=false）：只看 DOM，回合结束以"继续工作"按钮出现为准，文本长时间稳定兜底
     - PARTIAL_ON_TIMEOUT=false：超时就明确报错，而不是把半截回答转发给对方
   （--no-thresholds 可以只改中文选择器不改阈值；上游自带的 jsdom 测试在放宽阈值后会超时，这是预期的）
  1b) extensions/shared/content.js（两处小改，均有幂等标记）
     - 每次发送前先看一眼：上一轮留下的"此任务成功了吗? 是/否/继续工作"问卷若还开着，先点"继续工作"再打字
       （原版只在答完后的 15 秒内找一次问卷；Arena 的问卷有时来得晚，下一轮就会发不出去）
     - 点"继续工作"后若问卷没消失，再依次补一次原生 click() 和键盘回车
  2) .env（没有就从 .env.example 复制）
     - AAB_REQUEST_TIMEOUT=3600      服务器最多等页面 1 小时
     - AAB_AGENT_WRAPPER={last_user} 原样把最后一条消息打进页面（去掉原项目那段"你是被程序调用的工具"前言，
                                      但仍保留 agent 模式，这样扩展才会自动点"继续工作"）
     - AAB_SANITIZE_MODE=detect      只报告不改写回答里的"危险命令"（我们不执行它们，改写反而会弄坏对话中的代码）

用法（在 ArenaAgentBridge 仓库根目录执行，或用 --repo 指定）：
    python patch_aab.py --repo D:\\arena-agent-bridge           # 打补丁 + 重新打包 dist/chrome
    python patch_aab.py --repo D:\\arena-agent-bridge --no-build
    python patch_aab.py --repo D:\\arena-agent-bridge --dry-run  # 只看会改什么
幂等：重复执行不会重复插入。
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

MARK = "[tabbit-arena-bridge]"

# 阈值：键 → 新值（毫秒/布尔）。理由见文件头。
BEHAVIOR = {
    "STABLE_MS": "120000",          # 文本 2 分钟不变且没有停止按钮 → 才算稳定结束（兜底，正常靠"继续工作"按钮判定）
    "SSE_IDLE_MS": "60000",         # （仅在你重新打开 capture 时有意义）数据流静默 1 分钟才走"流空闲"快速路径
    "ENABLED": "false",             # capture.ENABLED：关掉页面数据流钩子，只看 DOM。Agent 多步执行的流里"结束帧"不可靠，
                                    #   靠"继续工作"按钮出现（精确）+ 文本长时间稳定（兜底）来判定一轮结束更稳
    "STALL_MS": "1800000",          # 有停止按钮但文本 30 分钟不长 → 才返回半截
    "NO_OUTPUT_MS": "300000",       # 5 分钟一点输出都没有 → no_output
    "MAX_WAIT_MS": "3600000",       # 单次请求硬上限 1 小时（与 AAB_REQUEST_TIMEOUT 对应）
    "IDLE_STALL_MS": "1800000",     # 页面与数据流都 30 分钟没动静 → site_idle
    "KEEP_WORKING_WAIT_MS": "15000",  # 回答结束后最多找 15 秒"继续工作"按钮
    "PARTIAL_ON_TIMEOUT": "false",
}
ENV = {
    "AAB_REQUEST_TIMEOUT": "3600",
    "AAB_MAX_REQUEST_TIMEOUT": "3600",
    "AAB_AGENT_WRAPPER": "{last_user}",
    "AAB_SANITIZE_MODE": "detect",
}


def patch_config_js(text: str, thresholds: bool = True) -> tuple[str, list[str]]:
    changes = []
    if MARK in text:
        changes.append("config.js 已经打过补丁，跳过选择器部分")
    else:
        # 1) keepWorking：中文按钮
        kw_lines = ("        { css: 'button', text: ['继续工作'] }, // " + MARK + " 中文界面\n"
                    "        { css: '[role=\"button\"]', text: ['继续工作'] },\n")
        new, n = re.subn(r"(keepWorking:\s*\[\n)", lambda m: m.group(1) + kw_lines, text, count=1)
        if n:
            text = new; changes.append("keepWorking += '继续工作'")
        else:
            changes.append("!! 没找到 keepWorking 列表，请手工添加 { css: 'button', text: ['继续工作'] }")
        # 2) stopButton：中文
        sb_lines = ("        { css: 'button[aria-label*=\"stop\" i]' }, // " + MARK + "\n"
                    "        { css: 'button', text: ['停止生成'] },\n")
        new, n = re.subn(r"(stopButton:\s*\[\n)", lambda m: m.group(1) + sb_lines, text, count=1)
        if n:
            text = new; changes.append("stopButton += '停止生成' / aria-label*=stop")
        # 3) survey 清空
        new, n = re.subn(r"survey:\s*\[\n(?:.*\n)*?\s*\],",
                         lambda m: "survey: [], // " + MARK + " 只认“继续工作”按钮作为回合结束标志", text, count=1)
        if n:
            text = new; changes.append("survey 选择器清空（只认 keepWorking 按钮）")
        else:
            changes.append("!! 没找到 survey 列表，请手工把 selectors.survey 改成 []")
    # 4) 阈值
    for key, val in (BEHAVIOR.items() if thresholds else []):
        pat = re.compile(r"(\n\s*)" + key + r":\s*([A-Za-z0-9_]+),")
        m = pat.search(text)
        if not m:
            changes.append(f"!! 没找到 behavior.{key}，请手工改成 {val}")
            continue
        if m.group(2) == val:
            continue
        text = pat.sub(lambda mm: f"{mm.group(1)}{key}: {val}, /* {MARK} 原值 {mm.group(2)} */", text, count=1)
        changes.append(f"{key}: {m.group(2)} → {val}")
    return text, changes


CONTENT_ANCHOR_PRE = "      const input = await waitFor(() => SiteDriver.findInput(), {\n"
CONTENT_PRE = (
    "      // " + MARK + " pre-survey：上一轮留下的问卷还开着 → 先点“继续工作”，否则这一轮打字后发不出去\n"
    "      if ((payload.mode || 'agent') !== 'direct' && behavior.AUTO_KEEP_WORKING !== false && SiteDriver.hasSurvey()) {\n"
    "        const pre = await SiteDriver.acceptKeepWorking({ waitMs: 0 });\n"
    "        log('survey left over from the previous turn: %s', JSON.stringify(pre));\n"
    "        await sleep(800);\n"
    "      }\n"
)
CONTENT_ANCHOR_CLICK = (
    "      // Wait until the survey is gone (the site usually swaps the composer back).\n"
    "      const gone = await waitFor(() => !this.findKeepWorking(), { timeout: 6000, interval: 200 });\n"
)
CONTENT_CLICK = (
    "      // " + MARK + " robust-click：合成事件点不动时，再试原生 click() 和键盘回车\n"
    "      let gone = await waitFor(() => !this.findKeepWorking(), { timeout: 2500, interval: 200 });\n"
    "      if (!gone) {\n"
    "        try { button.focus(); button.click(); } catch (_) { /* ignore */ }\n"
    "        log('survey still visible after synthetic click, retried with native click()');\n"
    "        gone = await waitFor(() => !this.findKeepWorking(), { timeout: 2500, interval: 200 });\n"
    "      }\n"
    "      if (!gone) {\n"
    "        try {\n"
    "          button.focus();\n"
    "          for (const type of ['keydown', 'keypress', 'keyup']) {\n"
    "            button.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true }));\n"
    "          }\n"
    "        } catch (_) { /* ignore */ }\n"
    "        log('survey still visible after native click, retried with Enter');\n"
    "        gone = await waitFor(() => !this.findKeepWorking(), { timeout: 3000, interval: 200 });\n"
    "      }\n"
)
# 问卷右上角有 Esc 提示：前面都点不动时按一次 Esc 把它关掉（不投票，但输入框会回来）
CONTENT_ESCAPE_ANCHOR = "      return { found: true, clicked: true, cleared: Boolean(gone), label };\n"
CONTENT_ESCAPE = (
    "      if (!gone) { // " + MARK + " escape-fallback\n"
    "        try {\n"
    "          for (const type of ['keydown', 'keyup']) {\n"
    "            document.dispatchEvent(new KeyboardEvent(type, { key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true, cancelable: true, composed: true }));\n"
    "          }\n"
    "        } catch (_) { /* ignore */ }\n"
    "        log('survey still visible after Enter, pressed Escape');\n"
    "        gone = await waitFor(() => !this.findKeepWorking(), { timeout: 3000, interval: 200 });\n"
    "      }\n"
)


def patch_content_js(text: str) -> tuple[str, list[str]]:
    changes = []
    if MARK + " pre-survey" in text:
        changes.append("content.js 已有 pre-survey 补丁，跳过")
    elif CONTENT_ANCHOR_PRE in text:
        text = text.replace(CONTENT_ANCHOR_PRE, CONTENT_PRE + CONTENT_ANCHOR_PRE, 1)
        changes.append("content.js: 发送前先清掉上一轮遗留的问卷（pre-survey）")
    else:
        changes.append("!! content.js 里没找到 pre-survey 的插入点（上游改了代码），请把 content.js 发给我")
    if MARK + " robust-click" in text:
        changes.append("content.js 已有 robust-click 补丁，跳过")
    elif CONTENT_ANCHOR_CLICK in text:
        text = text.replace(CONTENT_ANCHOR_CLICK, CONTENT_CLICK, 1)
        changes.append("content.js: 点“继续工作”后没消失则补原生 click()/回车（robust-click）")
    else:
        changes.append("!! content.js 里没找到 robust-click 的插入点（上游改了代码），请把 content.js 发给我")
    if MARK + " escape-fallback" in text:
        changes.append("content.js 已有 escape-fallback 补丁，跳过")
    elif MARK + " robust-click" in text and CONTENT_ESCAPE_ANCHOR in text:
        text = text.replace(CONTENT_ESCAPE_ANCHOR, CONTENT_ESCAPE + CONTENT_ESCAPE_ANCHOR, 1)
        changes.append("content.js: 点不动时最后按一次 Esc 关掉问卷（escape-fallback）")
    return text, changes


def patch_env(repo: Path, dry: bool) -> list[str]:
    changes = []
    env = repo / ".env"
    if not env.exists():
        example = repo / ".env.example"
        if example.exists():
            if not dry:
                shutil.copy(example, env)
            changes.append(".env 不存在，已从 .env.example 复制")
        else:
            if not dry:
                env.write_text("", encoding="utf-8")
            changes.append(".env 不存在，已新建")
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    out, seen = [], set()
    for line in lines:
        m = re.match(r"^\s*#?\s*(AAB_[A-Z_]+)\s*=", line)
        key = m.group(1) if m else None
        if key in ENV and not line.lstrip().startswith("#"):
            if line.strip() != f"{key}={ENV[key]}":
                changes.append(f".env {key}: {line.strip()} → {key}={ENV[key]}")
            out.append(f"{key}={ENV[key]}")
            seen.add(key)
        else:
            out.append(line)
    missing = [k for k in ENV if k not in seen]
    if missing:
        out.append("")
        out.append(f"# --- {MARK} Arena↔Tabbit 长对话用 ---")
        for k in missing:
            out.append(f"{k}={ENV[k]}")
            changes.append(f".env += {k}={ENV[k]}")
    if not dry:
        env.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changes


def main():
    ap = argparse.ArgumentParser(description="给 ArenaAgentBridge 打补丁（中文界面 + 长时间等待）")
    ap.add_argument("--repo", default=".", help="ArenaAgentBridge 仓库根目录（含 extensions/ 与 server/）")
    ap.add_argument("--no-build", action="store_true", help="打完补丁不重新打包扩展")
    ap.add_argument("--dry-run", action="store_true", help="只显示会改什么，不写文件")
    ap.add_argument("--no-thresholds", action="store_true", help="只改中文选择器，不放宽等待阈值（阈值也可以之后在扩展的选项页里改）")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    cfg = repo / "extensions" / "shared" / "config.js"
    if not cfg.exists():
        sys.exit(f"找不到 {cfg}，--repo 要指向 ArenaAgentBridge 仓库根目录")

    text = cfg.read_text(encoding="utf-8")
    new_text, changes = patch_config_js(text, thresholds=not args.no_thresholds)
    if not args.dry_run and new_text != text:
        backup = repo / "extensions" / "config.js.orig"   # 放在 shared/ 外面，免得被打包进 dist/
        if not backup.exists():
            shutil.copy(cfg, backup)
        cfg.write_text(new_text, encoding="utf-8")
    content = repo / "extensions" / "shared" / "content.js"
    if content.exists():
        ctext = content.read_text(encoding="utf-8")
        cnew, cchanges = patch_content_js(ctext)
        changes += cchanges
        if not args.dry_run and cnew != ctext:
            cbackup = repo / "extensions" / "content.js.orig"
            if not cbackup.exists():
                shutil.copy(content, cbackup)
            content.write_text(cnew, encoding="utf-8")
    else:
        changes.append("!! 找不到 extensions/shared/content.js")
    changes += patch_env(repo, args.dry_run)

    print(("[dry-run] " if args.dry_run else "") + "改动：")
    for c in changes:
        print("  - " + c)
    if any(c.startswith("!!") for c in changes):
        print("\n注意：上面带 !! 的项目需要你手工处理（上游项目可能改了文件格式）。")

    if args.dry_run or args.no_build:
        return 0
    build = repo / "scripts" / "build-extensions.py"
    if build.exists():
        print("\n重新打包扩展：python scripts/build-extensions.py")
        r = subprocess.run([sys.executable, str(build)], cwd=str(repo))
        if r.returncode != 0:
            print("!! 打包失败，请看上面的报错"); return r.returncode
        print(f"\n完成。到浏览器 chrome://extensions → 开发者模式 → 加载已解压的扩展程序 → 选择：\n  {repo / 'dist' / 'chrome'}\n"
              f"（如果之前已经加载过，点扩展卡片上的“重新加载”即可）\n"
              f"然后重启服务器：python -m server   （.env 已更新，需要重启才生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
