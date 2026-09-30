# 方案 v2：现成项目拼装版（Arena 侧用 ArenaAgentBridge，Tabbit 侧用 tabbit_web_api.py）

> 2026-09-29 更新。按你的要求先搜了 GitHub / Greasy Fork，结论：**Arena 侧有现成可用的项目，Tabbit 网页侧没有**。
> 所以 v2 = 现成的 ArenaAgentBridge（负责 Arena Agent 网页）+ 我们自己的 CDP 小桥（负责 Tabbit 对话页）+ 编排器（来回转发、停机通知）。
> 三个部件之间全是"OpenAI 兼容接口"，谁坏了只换谁。原来的纯 CDP 双侧方案（README.md）保留作为退路。
>
> **澄清：这里没有任何云 API、不要 key、不花钱、不换模型。**"OpenAI 兼容接口"只是本机三个程序之间**传字的格式**
> （地址全是 127.0.0.1，出不了你这台电脑）。真正干活的仍然是 **Tabbit 里打开的 arena.ai Agent 网页**（扩展替你打字、读回答）
> 和 **web.tabbit.ai 对话页里你自己选的模型**（CDP 替你打字、读回答）。`A_API_KEY` 之类的变量随便填或不填。

```
┌ Tabbit（--remote-debugging-port=9223）───────────────────────────────────────────────┐
│  标签页 A：arena.ai/agent/<会话>        ←── ArenaAgentBridge 扩展（装在 Tabbit 里）      │
│  标签页 B：web.tabbit.ai/session/<会话> ←── CDP（tabbit_web_api.py 直接操作页面）        │
└───────────────▲──────────────────────────────────────────▲───────────────────────────┘
                │ ws 127.0.0.1:8000                          │ CDP 127.0.0.1:9223
     ArenaAgentBridge 服务器                      tabbit_web_api.py
     http://127.0.0.1:8000/v1  (模型 arena-agent)  http://127.0.0.1:8124/v1  (模型 tabbit-web)
                ▲                                            ▲
                └──────────── orchestrator.py（裁判 + 邮差 + 记录 + 停机通知）──────────────┘
```

---

## 0. 搜索结论（为什么这么拼）

| 需求 | 找到了什么 | 结论 |
| --- | --- | --- |
| 自动操作 **arena.ai Agent 模式** 网页 | **[startify2647/ArenaAgentBridge](https://github.com/startify2647/ArenaAgentBridge)**（MIT，v1.5.1，2026-09-24 还在更新）：本地 FastAPI 服务器 + Chrome/Edge/Firefox 扩展，把已登录的 arena.ai/agent 标签页包装成 OpenAI 接口。已经处理了我们最头疼的几件事：答完后的"继续工作"问卷自动点掉、用 Stop 按钮/文本稳定判定回合结束、验证码/登出/改版分别报错、弹窗里有 **Diagnose DOM** 一键诊断、选择器全在一个 `config.js` 里。 | **直接用**（打 3 个小补丁：中文按钮、放宽等待时间、去掉它自带的"你是工具"前言） |
| 自动操作 **web.tabbit.ai 对话页** | 没有现成的网页自动化项目。只有 [hih24337/tabb2 (Tabbit2API)](https://github.com/hih24337/tabb2)：把 Tabbit **内部 API** 转成 OpenAI 接口（需要抓 Tabbit 的 access token）。但它 README 昨天被改成所有链接都指向一个 zip 包、话题标签是一堆无关的垃圾词——**不建议下载运行**。 | **继续用我们的 CDP 桥**，只是包成一个接口（`tabbit_web_api.py`），选择器仍需你抓两个状态给我 |
| Tabbit 官方自动化 | [Tabbit-Browser/Tabbit-Devtools-Skill](https://github.com/Tabbit-Browser/Tabbit-Devtools-Skill)（官方）：确认 Tabbit 支持远程调试，开关在 `tabbit://inspect/#remote-debugging`，端口写在 `%LOCALAPPDATA%\Tabbit Browser\User Data\DevToolsActivePort`；Tabbit ≥1.9 还内置 `tabbit-cli`（Playwright 运行时）。 | 你现在用 `--remote-debugging-port=9223` 已经能用，不必改 |
| 篡改猴脚本"两个 AI 标签页互转" | Greasy Fork 上 arena.ai 相关只有导出/宽屏/模型列表/一个走 LMArena 老路线的"Arena API Bridge"，**没有**中转类脚本；也没有通用的"两个网页 AI 互聊"脚本。 | 不存在，放弃这条 |

**诚实的风险提示**：ArenaAgentBridge 只有十天历史、约 20 次提交、作者用 Arena 自己的 Agent 生成了大部分代码；我在沙箱里跑通了它的服务器、打包和 200 多项自带测试，但**没有真实的 arena.ai 页面可试**，第一次上真页面出问题很正常——它的 **Diagnose DOM** 就是干这个用的。

---

## 1. 装 ArenaAgentBridge（Arena 侧，约 10 分钟）

前提：Python 3.10+；git（没有就到 GitHub 页面 Code → Download ZIP 解压）。

```powershell
cd G:\
git clone https://github.com/startify2647/ArenaAgentBridge arena-agent-bridge
cd G:\arena-agent-bridge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r server\requirements.txt

# 打补丁 + 重新打包扩展（脚本在本仓库 aab\ 目录；可加 --dry-run 先看会改什么）
python G:\tabbit-arena-bridge\aab\patch_aab.py --repo G:\arena-agent-bridge

# 启动服务器（保持这个窗口开着）
python -m server
```

补丁做了什么（详见 `aab\patch_aab.py` 文件头）：
1. `extensions\shared\config.js`：`keepWorking` 加上中文 **"继续工作"**（原版只认英文 "Keep working"，中文界面下答完一轮输入框不会回来）；`stopButton` 加 "停止生成"；`survey` 选择器清空，只用"继续工作"按钮当"回合结束"信号；**等待阈值全部放宽**（文本稳定 2 分钟、无输出 5 分钟、单次上限 1 小时、页面/数据流 30 分钟不动才算挂）；关闭页面数据流钩子只看 DOM；超时时明确报错而不是把半截回答转发出去。
2. `extensions\shared\content.js`（两处小改）：**每次发送前先看一眼**，上一轮留下的"此任务成功了吗? 是/否/继续工作"问卷若还开着，先点"继续工作"再打字（原版只在答完后的 15 秒内找一次问卷，Arena 的问卷有时来得晚，下一轮就发不出去——实测踩到过）；点了没消失再补原生 `click()` 和键盘回车。
3. `.env`：`AAB_REQUEST_TIMEOUT=3600`、`AAB_AGENT_WRAPPER={last_user}`（原样把消息打进页面，去掉它自带的"你是被程序调用的工具，只输出最终答案"前言）、`AAB_SANITIZE_MODE=detect`（只报告不改写回答里的"危险命令"——我们不执行它们，改写反而会弄坏对话里的代码）。

**把扩展装进 Tabbit**（也可以装进 Edge/Chrome，让 Arena 开在那边；服务器只认 127.0.0.1:8000，浏览器无所谓）：
1. Tabbit 地址栏打开 `chrome://extensions`（打不开就试 `tabbit://extensions`）→ 右上角 **开发者模式** 开 → **加载已解压的扩展程序** → 选 `G:\arena-agent-bridge\dist\chrome`。
2. **先禁用你自己的篡改猴脚本**（arena.ai 上注入 `#amd-*` 按钮的那个），避免两个脚本抢输入框。
3. 打开（或刷新）Arena 的会话页 `https://arena.ai/agent/01a0eb33-…`，页面角落应出现小徽标 **bridge: connected**；点工具栏扩展图标 → 弹窗里状态应为 connected。若是 `browser_offline`：确认服务器窗口在跑、扩展加载的是 `dist\chrome` 不是源码目录。
4. 弹窗 → **Diagnose DOM**：看 `input`、`sendButton`、`assistantMessage`、`keepWorking` 各有几个匹配。Arena 现在的结构（你上次 probe 出来的 `div[data-agent-transcript-message]`、`button[aria-label="Send message"]`）都在它的默认选择器里；`keepWorking` 应能匹配到"继续工作"。

**冒烟测试**。最省事的是扩展弹窗的 **Quick test** 页签（改成中文 prompt → Send test），但注意：**弹窗一关（点了页面任何地方）测试请求就被取消、Agent 会被按停**（页面上会出现"Stopped"）。所以正式测试建议用管理面板 `http://127.0.0.1:8000/admin` 里的 **Playground**（不怕切窗口，History 里还能看到每次请求的 `stop_reason` 和 `kept_working`），或者 PowerShell：
```powershell
$body = @{ model = "arena-agent"; messages = @(@{ role = "user"; content = "请只回复：bridge ok" }); timeout = 600 } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/chat/completions -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
```
期望：Arena 页面里自动出现你这句话 → Agent 回答 → 答完后"继续工作"被自动点掉 → PowerShell 收到 JSON，`choices[0].message.content` 是回答，`x_bridge.browser_meta.stop_reason` 最好是 `survey`（说明是靠"继续工作"按钮精确判定的结束；若是 `stable` 说明是等文本 2 分钟不变兜底判定的，也能用，只是每轮多等 2 分钟）。

**再发第二句**（`content = "请只回复：第二句"`）确认：① 它接在**同一个 Arena 会话**里而不是新开会话；② 输入框在两次之间确实回来了。这两点通过，Arena 侧就搞定了，**不再需要抓 arena-1/2/3 那三个状态**。

调阈值不用改文件：扩展弹窗 → Settings/Options 里有全部阈值，改完立即生效。

---

## 2. Tabbit 侧：直接试（选择器已预填）

`config.json` 里 `tabbit` 一节的选择器已经按 **web.tabbit.ai 的前端代码**（2026-09-29 的生产包）填好，不需要先抓页面：

| 项 | 选择器 | 来源 |
| --- | --- | --- |
| 输入框 | `.tiptap[contenteditable='true']`（备选 `.ProseMirror`、`[data-chip-editor]`…） | 输入框是 Tiptap 富文本编辑器，所以用 `force_native_input: true`（CDP 真实打字） |
| 发送按钮 | `#ChatSendButton` | Tabbit 自己的代码就是 `document.getElementById("ChatSendButton").click()` 来发送；输入为空时按钮带 `data-send-blocked="true"` |
| 回答 | `[data-message-type='assistant']` | 每条消息的行都有 `data-message-type=user/assistant`、`data-message-id` |
| 正在生成 | `button > div.w-2\.5.h-2\.5.rounded-\[0\.09375rem\]` | 生成中发送按钮变成"停止"（里面一个 10×10 的方块），并且 `id` 消失 |

更新本仓库（`bridge.py` 需 ≥ 0.7，`python bridge.py --version` 可查），Tabbit 用 `--remote-debugging-port=9223` 启动，打开 `https://web.tabbit.ai/session/f4ece106-…` 那个会话页，然后：

```powershell
cd G:\tabbit-arena-bridge
python tabbit_web_api.py --config config.json --cdp http://127.0.0.1:9223 --port 8124
# 另一个窗口：
$body = @{ model = "tabbit-web"; messages = @(@{ role = "user"; content = "请只回复：bridge ok" }) } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://127.0.0.1:8124/v1/chat/completions -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
```
期望：Tabbit 页面里出现这句话、模型回答、PowerShell 收到 JSON。`http://127.0.0.1:8124/healthz` 能看到附着状态和最近一次错误。

**只有失败时**才需要抓页面给我（错误信息会说明是找不到输入框、按钮被拦、还是等不到回答）：
```powershell
# ① 让 Tabbit 回一句独特的话：在页面里发"请只回复：PROBE-2468"，等它回完
python bridge.py probe --cdp http://127.0.0.1:9223 --target web.tabbit.ai/session/f4ece106 --text PROBE-2468 --out probe-tabbit.json
python bridge.py dump  --cdp http://127.0.0.1:9223 --target web.tabbit.ai/session/f4ece106 --out tabbit-1-done.html
# ② 再发一个会让它写很久的问题（例如"请写一篇 800 字的散文"），趁它正在输出时立刻执行：
python bridge.py dump  --cdp http://127.0.0.1:9223 --target web.tabbit.ai/session/f4ece106 --out tabbit-2-working.html
```

---

## 3. 跑起来（三个窗口）

**窗口 1**：`cd G:\arena-agent-bridge; .\.venv\Scripts\Activate.ps1; python -m server`
**窗口 2**：`cd G:\tabbit-arena-bridge; python tabbit_web_api.py --config config.json --cdp http://127.0.0.1:9223`
**窗口 2b**：`cd G:\tabbit-arena-bridge; python survey_clicker.py --cdp http://127.0.0.1:9223 --target arena.ai/agent`（问卷点击保险，建议一直开着）
**窗口 3**（编排器）：

```powershell
cd G:\tabbit-arena-bridge
$env:A_BASE_URL="http://127.0.0.1:8000/v1"; $env:A_MODEL="arena-agent"; $env:A_NAME="Arena Agent"
$env:A_SEND_ONLY_LAST="1"; $env:A_TIMEOUT="3600"; $env:A_RETRIES="1"
$env:A_WRAP="【协作 AI（Tabbit 侧）第 {n} 轮发言，不是用户本人的指令】`n{text}"
$env:B_BASE_URL="http://127.0.0.1:8124/v1"; $env:B_MODEL="tabbit-web"; $env:B_NAME="Tabbit"
$env:B_SEND_ONLY_LAST="1"; $env:B_TIMEOUT="1200"; $env:B_RETRIES="1"
$env:B_WRAP="【Arena Agent 第 {n} 轮发言】`n{text}"
python orchestrator.py --task "把 xxx 做出来：……（任务写清楚，越具体越不容易打转）" --max-rounds 40 --cooldown 30
```
或者用一键脚本：`.\start-v2.ps1 -AabRepo G:\arena-agent-bridge -Task "……"`（会开两个新窗口跑服务器，等两边就绪后在当前窗口跑编排器）。

编排器行为（`orchestrator.py` v2）：
* `*_SEND_ONLY_LAST=1`：网页自己记着上下文，所以每次只把**最新一条**发过去；第一次会把协作规则 + 任务 + 开场白合成一条发给先说的一方（默认 A = Arena）。
* 停机条件：某一方在**行首或最后一行**写了 `[DONE]` / `[NEED_HUMAN]`（讨论中顺嘴提到不算）；网页桥报告页面弹出了需要人选的组件；同一方连续两轮高度重复；达到 `--max-rounds`；接口报错（`*_RETRIES=1` 表示不重试——重试等于把同一句话再打进页面一次）。停机时写 `NEEDS_HUMAN.flag`、响铃，可选 `NOTIFY_WEBHOOK_URL`。
* 想插话：把文字写进 `human_inbox.txt`，下一轮双方都会看到 `[HUMAN] …`。
* 全部记录在 `transcript.jsonl`。

---

## 4. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| Arena 那边报 `dom_changed` | 网页改版或选择器没匹配。扩展弹窗 → Diagnose DOM 看哪一项是 0 → 在 `extensions\shared\config.js` 里加一条选择器 → `python scripts\build-extensions.py` → 扩展页点"重新加载"。 |
| `browser_offline` | 扩展没连上服务器：服务器没开 / 加载的不是 `dist\chrome` / Arena 标签页不在 `arena.ai/agent` 路径下。 |
| `captcha_required` / `login_required` | 人工在页面里处理，它不会替你过验证码。 |
| `site_idle` / `no_output` | 页面很久没动静（网络断、被后台休眠、会话被 Arena 结束）。把 Arena 标签页**单独放一个窗口**、Tabbit 的"后台智能休眠"对这两个页面关掉。 |
| 不知道卡在哪 | 先跑 `python doctor.py --aab-repo G:\arena-agent-bridge`，照"结论 / 下一步"做；要发给 AI 看就加 `--out doctor.json` |
| 答完后"此任务成功了吗?"问卷一直挂着，下一句发不出去 | 扩展的合成点击 Arena 不认。另开一个窗口跑 `python survey_clicker.py --cdp http://127.0.0.1:9223 --target arena.ai/agent`（`start-v2.ps1` 默认会一起拉起）。它先打印找到的元素（标签/层级/HTML），5 秒后真实点击，日志里出现 `✔ 问卷已消失` 即可 |
| 管理面板 Playground 的 Send 置灰 | 说明上一条请求还在跑（单飞；等页面回答最多可等到 `no_output` 5 分钟）或浏览器没连上（扩展"重新加载"后必须刷新 Arena 标签页）。Overview 里看 in-flight / browser 状态；卡住就点 Browser 区域的 **Cancel**，或 Ctrl+C 重启服务器 |
| 每轮都要等 2 分钟才返回 | `stop_reason` 是 `stable` 而不是 `survey`：说明"继续工作"按钮没被识别。用 Diagnose DOM 看 `keepWorking` 匹配数；若按钮文字不是"继续工作"，把实际文字加进 `config.js` 的 `keepWorking`。 |
| Arena 一段时间后强制"新建对话" | 社区反馈 Agent 单会话有条数上限（未证实）。届时输入框消失 → `dom_changed` → 编排器停机通知你；新开会话后重跑即可（`--opener-file` 可把之前的进展总结当开场白喂进去）。 |
| 想让 Tabbit 先说 | `--first-speaker B`。 |
| 想换 Tabbit 侧为官方 API | 只需把 `B_BASE_URL/B_MODEL/B_API_KEY` 指向任意 OpenAI 兼容接口，并去掉 `B_SEND_ONLY_LAST`。 |

---

## 5. 文件

| 文件 | 作用 |
| --- | --- |
| `aab\patch_aab.py` | 给 ArenaAgentBridge 打补丁并重新打包（幂等；`--dry-run` / `--no-thresholds` / `--no-build`） |
| `tabbit_web_api.py` | 把 Tabbit 对话页包成本机文字接口（依赖同目录 `bridge.py` ≥ 0.7） |
| `test/tabbit-real.html` | 按 web.tabbit.ai 真实结构做的模拟页（Tiptap 输入框、`#ChatSendButton`、`data-message-type`、生成中的停止方块），`tabbit_web_api.py` 对它连发两句通过 |
| `survey_clicker.py` | 盯着 Arena 页面，"此任务成功了吗?"问卷出现 5 秒后按阶梯把它点掉：真实鼠标点击 → 聚焦后真实键盘 Enter/Space → 直接调 React 的 onClick → 真实 Esc → 点卡片的 ×。扩展在页面里发的合成点击 Arena 可能不认（实测卡在这里），这个走浏览器输入通道，和你亲手点一样。每一步都打印结果，问卷消失后还会报告输入框/发送按钮是否回来了 |
| `doctor.py` | 一键诊断："为什么发不出去 / Send 为什么是灰的"。查服务器、扩展连接、卡住的请求、最近 5 条请求的失败原因、Arena 页面此刻状态（问卷/输入框/发送按钮）、dist 里补丁是否齐全，最后给出下一步。`--fix` 顺手点掉问卷，`--cancel` 顺手取消卡住的请求，`--out doctor.json` 存原始数据 |
| `orchestrator.py` | 编排器 v2（与 `ai-pingpong\orchestrator.py` 是同一个文件） |
| `start-v2.ps1` | 一键拉起三个部件 |
| `bridge.py` / `config.json` / `README.md` | v1 纯 CDP 双侧方案（仍可用；`probe` / `dump` 也是 v2 抓 Tabbit 结构要用的） |

沙箱里已验证：ArenaAgentBridge 服务器（mock 浏览器模式）+ `tabbit_web_api.py`（连真实 Chromium 上的模拟页）+ `orchestrator.py` 三件套端到端跑通，包括 `[DONE]` 停机、单次 `timeout` 覆盖、并发第二个请求得到 429、超时返回 504 与流式输出。
