# 交接文档：Tabbit 对话模型 ⇄ Arena Agent 自动互通

> 更新：2026-09-29。给下一个接手的 AI / 人看的。读完本文 + `tabbit-arena-bridge/README-v2.md` 即可继续，不需要翻聊天记录。
> 用户以中文交流；回复请用中文，每次改动列出"📦 本轮更新的文件"，并让用户用 `--version` 核对（用户靠重新下载整个文件夹来更新，多次出现本地文件过期的问题）。

---

## 0. 一句话目标

让 **Tabbit 浏览器里的网页对话（web.tabbit.ai，用户自己选模型）** 和 **arena.ai 的 Agent 模式网页** 自动一问一答、来回转发，直到真的需要人拍板（[NEED_HUMAN]）或任务完成（[DONE]）才停下来通知用户。

**硬约束（用户明确要求，不要再问、不要绕开）：**
1. 两边都是**网页**，不用任何云 API、不花钱、不要 key。文档里出现的"OpenAI 兼容接口"只是本机几个小程序之间传字的格式（全在 127.0.0.1）；用户一度以为要买 API 而发火，务必说清。
2. Tabbit 用**对话模式**（自己选模型），**不用** Tabbit Agent 模式（不能选模型，用户认为太蠢/不可控）。
3. 用户已知晓并接受：自动化 arena.ai 违反其服务条款（可能封号）；网页改版会导致方案失效，届时重做即可。所以可以直接做 arena.ai 的 DOM 自动化，但保持适度的节流（cooldown、轮数上限）。
4. 用户要的是**具体、可操作的步骤**，一步一步来（"一个一个试"），不喜欢抽象讨论和反复要求抓数据。已经答应过的事尽量一次给全。

---

## 1. 当前架构（v2，已定，用户已同意）

```
Tabbit（--remote-debugging-port=9223）
 ├─ 标签页 A：https://arena.ai/agent/<会话>      ←── ArenaAgentBridge 扩展（装在 Tabbit 里，走 ws://127.0.0.1:8000）
 └─ 标签页 B：https://web.tabbit.ai/session/<会话> ←── CDP（tabbit_web_api.py 直接操作页面）
                 ▲                                            ▲
   ArenaAgentBridge 服务器 :8000/v1（模型 arena-agent）   tabbit_web_api.py :8124/v1（模型 tabbit-web）
                 ▲                                            ▲
                 └────────── orchestrator.py（来回转发、停机判定、记录、通知）──────────┘
   另加：survey_clicker.py —— 通过 CDP 发真实鼠标点击，点掉 Arena 答完后的"此任务成功了吗?"问卷（见 §4 未解决问题）
```

为什么这样拼（经用户同意，2026-09-29）：
- 用户要求先搜现成项目再动手。搜索结论：**Arena 侧有现成项目** [startify2647/ArenaAgentBridge](https://github.com/startify2647/ArenaAgentBridge)（MIT，v1.5.1，2026-09-24 更新；本地 FastAPI + MV3 扩展，把已登录的 arena.ai/agent 标签页包成 OpenAI 接口；自带"继续工作"问卷处理、Diagnose DOM、错误码）。**Tabbit 网页侧没有现成项目**（[hih24337/tabb2](https://github.com/hih24337/tabb2) 是 Tabbit 内部 API 转接，README 被改成指向可疑 zip、话题标签是垃圾词，**不要用**）。Greasy Fork 上没有"两个 AI 标签页互转"类脚本。
- v1（纯 CDP 双侧，`bridge.py run`）保留作退路，见 `tabbit-arena-bridge/README.md`。它需要用户抓 Arena 的 3 个页面状态、由 AI 盲写选择器，所以才换成 v2。

---

## 2. 用户环境（事实，勿再问）

| 项 | 值 |
| --- | --- |
| 系统 / 终端 | Windows，PowerShell。控制台是 GBK：Python 脚本已 `reconfigure(errors="replace")`，输出文件一律用 `--out`，别用 `\| clip` |
| 本仓库位置 | `G:\tabbit-arena-bridge`（用户把整个文件夹下载覆盖来更新） |
| ArenaAgentBridge 位置 | 文档按 `G:\arena-agent-bridge` 写（venv 在 `.venv`），用户可能放在别处 |
| Tabbit | `D:\Program Files\Tabbit\Application\Tabbit Browser.exe`，用 `--remote-debugging-port=9223` 启动即可（**不需要** `--user-data-dir`，登录态保留；Tabbit 也有 `tabbit://inspect/#remote-debugging` 开关和 `%LOCALAPPDATA%\Tabbit Browser\User Data\DevToolsActivePort`） |
| Tabbit 对话页 | `https://web.tabbit.ai/session/f4ece106-…`（标题曾为"收到"；完整地址只在用户本机的 Tabbit 里）；另有一个 `7a3601b9-…` 会话不用。CDP 里都是普通 `page` 目标 |
| Arena 会话页 | 最近用的是 `https://arena.ai/agent/01a0eb9e-…`（更早的 `01a0eb33-…`）。是用户的另一个 Arena 账号，界面是**中文** |
| 干扰项 | 用户在 Tabbit 的篡改猴里有一个 arena.ai 用户脚本（注入 `#amd-*` 按钮），测试时必须禁用 |
| 端口约定 | CDP 9223；ArenaAgentBridge 8000；tabbit_web_api 8124；(ai-pingpong 的 mock 服务器 8123) |

---

## 3. 各部件状态（截至 2026-09-29）

| 部件 | 状态 | 证据 |
| --- | --- | --- |
| ArenaAgentBridge 服务器 + 扩展（打过我们的补丁） | **用户已装好、已连上**。Quick test 第 1 句 `请只回复：bridge ok` 成功往返 | Diagnose DOM：`input: true (textarea)`、`assistantMessage: 32`、`surveyVisible: true`、`keepWorkingVisible: true`、`page hook: inactive`（我们故意关的） |
| 答完后的问卷自动点掉 | **❌ 未解决（当前唯一阻塞点）**，详见 §4 | 用户：不手动点"继续工作"，第 2 句发不出去；管理面板 Playground 的 Send 置灰 |
| `survey_clicker.py` v0.2（CDP 阶梯：真实点击→键盘→React 句柄→Esc→×） | 已交付，**用户尚未反馈** | 沙箱 `test/survey.html?mode=trusted/key/react/escape/close` 五种"只有某一招有效"的模拟页全部通过，且从不误点 是/否 |
| `doctor.py` v0.1（一键诊断 + `--fix`/`--cancel`） | 已交付，**用户尚未运行** | 沙箱：对 AAB mock 服务器（含 40s 卡住的请求）+ 模拟页验证；服务器/CDP 不在时的错误路径正常；补丁检查 0/3→3/3 正确 |
| `tabbit_web_api.py` | 代码完成；**真实页面尚未试过**，但选择器已不是猜的 | 沙箱：对按真实结构做的 `test/tabbit-real.html` 连发两句通过（Tiptap 打字、`#ChatSendButton`、停止方块 busy 判定、多行文本保真）；旧模拟页回归通过（含 429/504/流式） |
| `config.json` 的 `tabbit` 一节 | **已按 web.tabbit.ai 生产前端包填好**（见 §6b） | 用户直接跑 §8 的 Tabbit 冒烟；失败再抓 probe/dump |
| `orchestrator.py` v2 | 完成；mock + 模拟页端到端通过 | `/tmp` 里跑过 A=AAB(mock) ⇄ B=tabbit_web_api(模拟页)，`[DONE]` 停机正常 |
| v1 `bridge.py run` | 只在模拟页测过 | — |
| Arena 端到端（真页面）两句连发 | **未通过** | 卡在问卷 |

---

## 4. 未解决问题：Arena 的"此任务成功了吗? 是 / 否 / 继续工作"问卷

**现象**：Agent 答完后底部出现该问卷（截图：深色卡片，右上角"Esc ×"，三行选项带图标）。问卷不点掉，下一条消息发不出去（输入框在 DOM 里存在——Diagnose 显示 `input: true`、里面残留 23 个字符——但被问卷压住，`sendButton: false`）。

**已知事实**
- 我们的中文选择器补丁有效：`keepWorkingVisible: true`（注意 `selector hits` 里的 `keepWorking: 412` 是不带文字过滤的计数，没有意义；看 `keepWorkingVisible`）。
- 扩展点按钮的方式是 JS 合成事件（`content.js` 的 `SiteDriver.click`：pointerdown/mousedown/pointerup/mouseup/click，非 isTrusted）。
- 扩展原逻辑只在答完后 `KEEP_WORKING_WAIT_MS` 内找一次问卷（我们已改成 15s），下一次发送前不检查。
- `last message sample: Stopped`：是因为弹窗 Quick test 在弹窗关闭时会取消请求并按停 Agent，不是 bug。以后测试用管理面板 `http://127.0.0.1:8000/admin` 的 Playground 或 PowerShell。
- Playground 的 Send 置灰 = 上一条请求还在跑（单飞；我们把 `NO_OUTPUT_MS` 放到 5 分钟，所以失败得慢）或浏览器断连（扩展"重新加载"后必须刷新 Arena 标签页）。Overview 看状态，Browser 区域有 Cancel。

**已做的尝试（按时间）**
1. `config.js`：keepWorking 加 `{css:'button', text:['继续工作']}`、`[role="button"]` 同款；survey 选择器清空 → 选择器匹配成功，但问卷没被点掉。
2. `content.js`（`aab/patch_aab.py` 自动打）：
   - pre-survey：每次发送前 `hasSurvey()` 为真就先 `acceptKeepWorking({waitMs:0})`；
   - robust-click：合成点击后 2.5s 没消失 → `button.focus(); button.click()` → 再没消失 → 键盘 Enter → 再没消失 → 对 document 发 Escape。
   用户反馈"还是不行"，但**不确定用户是否重跑了补丁并重新加载扩展**（验证方法：`Select-String -Path G:\arena-agent-bridge\dist\chrome\content.js -Pattern "pre-survey"` 有输出；Arena 标签页 F12 控制台过滤 `ArenaAgentBridge` 应能看到 `survey left over from the previous turn` 之类日志）。
3. `survey_clicker.py`：绕开扩展，用 CDP 发真实输入。v0.2 是阶梯：`Input.dispatchMouseEvent` 点按钮中心/文字中心 → `el.focus()` + 真实 Enter/Space → 调元素上 `__reactProps$*` 的 onClick/onPointerDown → 真实 Esc → 点卡片 ×（`aria-label` 含 close / 文字 Esc）。每一步都打印。**等用户结果。**
4. `doctor.py`：一次跑完"服务器 / 扩展连接 / 卡住的请求 / 最近请求失败码 / 页面状态 / dist 补丁"六项检查并给出下一步；`--fix` 立即点掉问卷，`--cancel` 取消卡住的请求（`POST /admin/api/browser/cancel`）。**用户尚未运行。**
   已从上游源码确认：管理面板 Playground 的 Send 只在它自己那条请求未返回（`chat.running`）时置灰；服务器一次只放一条请求进页面，扩展在"输入框被清空"时就当作已发出，然后等 `NO_OUTPUT_MS`（我们改成 5 分钟）才报 `no_output`——所以问卷挡住时，症状就是"Send 灰 5 分钟"。

**下一步（按优先级）**
1. 让用户先跑 `python doctor.py --aab-repo G:\arena-agent-bridge --out doctor.json`（必要时加 `--cancel --fix`），把输出贴回来——它会直接说明 Send 灰的原因、补丁是否真的加载、问卷是否在。
2. 让用户开着 `python survey_clicker.py --cdp http://127.0.0.1:9223 --target arena.ai/agent`，Playground 连发两句。看它打印的 `层级 / 卡片 / HTML` 行和"第 N 步后消失"——N 告诉我们 Arena 到底认哪一招（1–3 鼠标、4–5 键盘、6 React、7 Esc、8 ×）。
3. 若八招全失败（日志"所有招式都试过问卷仍在"）：看 `covered`（被别的层挡住）、`视口内`；试 `--bring-to-front`；观察用户手点时 Network 里发了什么请求（能否用 fetch 复现）；最后手段是在扩展里把问卷元素直接 `remove()` 并把输入框 `display` 改回来（未验证 Arena 是否允许在问卷未答时发送）。
3. 若问卷来得晚：clicker 常驻就能覆盖；也可把 pre-survey 逻辑并进 `tabbit_web_api.py`/编排器（发 Arena 前先让 clicker `--once`）。
4. 问卷解决后，回到主线：`tabbit_web_api.py` 对真实 Tabbit 页冒烟（选择器已预填，见 §6b；失败再抓结构）→ 三窗口（或 `start-v2.ps1`）跑一个简单任务 → 看 `transcript.jsonl`，调 `--cooldown`、`max-rounds`；留意社区传言"Agent 单会话约 5 条后强制新建对话"（未证实），若发生会以 `dom_changed` 停机。

---

## 5. 文件清单（工作区根目录）

```
Arena-Agent与Tabbit模型自动互通可行性分析.md   最初的可行性报告（路线 A–E、风险、引用）
HANDOFF.md                                    本文
ai-pingpong/
  orchestrator.py    编排器 v2（与 tabbit-arena-bridge/orchestrator.py 相同，改一处要同步两处）
  mock_openai_server.py, README.md
tabbit-arena-bridge/
  README-v2.md       ★ 现行操作手册（安装 AAB、打补丁、装扩展、冒烟测试、抓 Tabbit 结构、三窗口运行、FAQ）
  README.md          v1 纯 CDP 双侧方案（§2 调试端口、§4 list、§5 probe/dump 在 v2 里仍然要用）
  bridge.py          v0.7：CDP 基础库 + list/probe/dump/run 子命令。tabbit_web_api.py / survey_clicker.py / doctor.py 都 import 它
                     0.7：选择器按逗号顺序取第一段有可见命中的（pick）；force_native_input 改为"清空 + CDP 打字"（原来会插两遍）；data-send-blocked 视为禁用
  tabbit_web_api.py  v0.1：Tabbit 对话页 → OpenAI 兼容接口（:8124）。默认只发最后一条 user 消息；请求体 timeout 覆盖等待；并发 429；超时 504；human_needed 组件出现时在回复末尾追加 [NEED_HUMAN]
  survey_clicker.py  v0.2：CDP 阶梯点掉 Arena 问卷（--strategies click,key,react,escape,close / --delay / --once / --dry-run / --bring-to-front / --text / --title）
  doctor.py          v0.1：一键诊断 + --fix（点掉问卷）+ --cancel（取消卡住的请求）+ --out doctor.json
  orchestrator.py    同 ai-pingpong/orchestrator.py
  aab/patch_aab.py   给 ArenaAgentBridge 打补丁并重新打包（幂等；--dry-run/--no-thresholds/--no-build）
  start-v2.ps1       一键拉起：AAB 服务器 + tabbit_web_api + survey_clicker（-NoClicker 关）+ 编排器
  start-tabbit.ps1 / diag-tabbit.ps1   v1 时期的 Tabbit 启动/诊断脚本
  config.json        cdp_url 9223；arena/tabbit 的 target_match 已填；tabbit 选择器待填
  config.example.json
  test/arena.html, tabbit.html, survey.html, tabbit-real.html, config.test.json
                     模拟页（survey.html?mode=trusted|key|react|escape|close 各只认一招，用来验证 clicker 阶梯；tabbit-real.html 按 web.tabbit.ai 真实结构做）
uploads/probe-arena.json   用户真实 Arena 页面（答完状态）的 probe 结果
```

---

## 6. ArenaAgentBridge 相关知识（省得再读一遍源码）

- 仓库 `startify2647/ArenaAgentBridge`，v1.5.1。服务器 `python -m server`（FastAPI，127.0.0.1:8000），扩展源码 `extensions/shared/*.js`，打包 `python scripts/build-extensions.py` → `dist/chrome`（chrome://extensions → 开发者模式 → 加载已解压）。管理面板 `/admin`（Playground、History、Browser 控制）。`AAB_MOCK_BROWSER=1` 可无浏览器演示。
- 服务器把 `messages[]` 压成一段文字打进页面：`agent` 模式 = 前言 + 转录，`direct` 模式 = 只转录但**不处理问卷**（`survey: AUTO_KEEP_WORKING && mode !== 'direct'`）。所以我们保持 `agent` 模式、用 `AAB_AGENT_WRAPPER={last_user}` 去掉前言（所有 `AAB_*` 都可在 `.env` 设，`server/config.py`）。
- 一轮结束的判定（`content.js` capture 循环）：① 问卷出现（`hasSurvey()` = `findKeepWorking()` 或 survey 选择器）且文本静止 700ms → `survey`；② 无 Stop 按钮且文本 `STABLE_MS` 不变 → `stable`；③ 数据流空闲（我们已关 `capture.ENABLED`，不走）；④ `STALL_MS`/`IDLE_STALL_MS`/`NO_OUTPUT_MS` 各种超时。答完后 `handOff()` → `acceptKeepWorking()` 点"继续工作"。
- 我们的补丁（`aab/patch_aab.py`，幂等标记 `[tabbit-arena-bridge]`）：
  - `config.js`：keepWorking/stopButton 中文；`survey: []`；`STABLE_MS 120000`、`SSE_IDLE_MS 60000`、`STALL_MS 1800000`、`NO_OUTPUT_MS 300000`、`MAX_WAIT_MS 3600000`、`IDLE_STALL_MS 1800000`、`KEEP_WORKING_WAIT_MS 15000`、`PARTIAL_ON_TIMEOUT false`、`capture.ENABLED false`。备份在 `extensions/config.js.orig`。
  - `content.js`：pre-survey（锚点 `const input = await waitFor(() => SiteDriver.findInput(), {`）、robust-click（锚点 `const gone = await waitFor(() => !this.findKeepWorking(), { timeout: 6000, interval: 200 });`）、escape-fallback（锚点 `return { found: true, clicked: true, cleared: Boolean(gone), label };`）。备份 `extensions/content.js.orig`。
  - `.env`：`AAB_REQUEST_TIMEOUT=3600`、`AAB_MAX_REQUEST_TIMEOUT=3600`、`AAB_AGENT_WRAPPER={last_user}`、`AAB_SANITIZE_MODE=detect`。
- 上游测试结果：`pytest tests/test_build.py tests/test_extension_static.py` 34 通过；jsdom 套件 `node tests/extension_dom_test.mjs`：只改选择器时 166/166，加 content.js 补丁后 159/161（挂的 2 项是"预先放一个假问卷、期望它别被点掉"的场景，与我们要的行为相反，属预期）；放宽阈值后该套件会超时，也是预期。
- 上游默认选择器已经包含 Arena 现结构：消息容器 `[data-agent-transcript-message]`（用户消息带 `[data-user-message-layout]`）、发送 `button[aria-label="Send message"]`、输入 `textarea`。若将来要回退到 v1 纯 CDP，这些可直接抄进 `config.json`。
- 扩展弹窗：Status / Quick test / Diagnose / Settings 四个页签；Diagnose 的 `selector hits` 不带文字过滤，看 `checks:` 里的布尔值。

---

## 6b. Tabbit 网页（web.tabbit.ai）的页面结构——从生产前端包里读出来的

获取方法（可复现，不需要登录）：`curl https://web.tabbit.ai/session/<任意uuid>` 得到 HTML → 里面的 `<script src=https://cdn.tabbit.ai/web-prod/_next/static/chunks/...>` 全部下载（会话页比首页多出约 54 个 chunk，含 `app/session/[id]/page-*.js`）→ grep。Next.js App Router + shadcn/Radix + Tiptap。

| 用途 | 事实 | 备注 |
| --- | --- | --- |
| 消息行 | `div[data-message-index][data-message-id][data-message-type=<type>]`，type 取值含 `user`、`assistant`、`tool`（还有 `reasoning` 等） | 用户消息正文 `[data-user-message-readonly-content]`；列表容器 `[data-testid="chat-message-list-scroll"]` |
| 消息状态（不在 DOM 上） | `status` ∈ `thinking / streaming / tool_calling / error /（完成）` | 只能靠发送按钮形态判断是否在生成 |
| 发送按钮 | `id="ChatSendButton"`，`type=button`；输入为空/不可发时 `data-send-blocked="true"`；**生成中同一个按钮变成停止键：`id` 消失，内部是 `div.w-2.5.h-2.5.shrink-0.rounded-[0.09375rem]`** | Tabbit 自己的"妙招"代码用 `document.getElementById("ChatSendButton")?.click()` 发送 → 合成 click 有效 |
| 输入框 | Tiptap（`.tiptap`，ProseMirror contenteditable），支持 @提及/技能 chip（`#mention-list`、`<tab-skill-node>`）；另有 `[data-chip-editor="true"][contenteditable="plaintext-only"][role="textbox"]` 的小编辑器 | 用 CDP `Input.insertText` 打字最稳（`force_native_input: true`） |
| 其他 | `data-testid`：`sidebar-conversation-search-input`、`mcp-picker-trigger`、`newtab-chat-content`、`show-widget-streaming-shimmer`（组件流式占位）等；`data-session-id` 在侧栏会话按钮上；`data-agent-mode` 是技能属性 | 复制 AI 回复有特殊处理（控制台日志是中文，"=====AI回复消息复制事件特殊处理====="） |

config.json 里对应：`input=".tiptap[contenteditable='true'], .ProseMirror[contenteditable='true'], [data-chip-editor='true'], div[contenteditable][role='textbox'], div[contenteditable='true'], textarea"`（bridge.py 0.7 起按逗号顺序取第一个命中的）、`send_button="#ChatSendButton, …"`、`send_method="click"`、`assistant_messages="[data-message-type='assistant']"`、`busy="button > div.w-2\.5.h-2\.5.rounded-\[0\.09375rem\], [data-testid='show-widget-streaming-shimmer']"`。
风险：Tailwind 类名（停止方块）随改版可能变；`assistant` 行的 innerText 可能带上操作按钮的文字；若同页存在多个 `.tiptap`，`pick` 取文档顺序最后一个可见的。

## 7. 我们自己代码里的关键设计决定

- **只发最新一条**（`*_SEND_ONLY_LAST=1`）：网页自己保存上下文；第一次调用把协作规则 + 任务 + 开场白合成一条。网页桥不重试（`*_RETRIES=1`，重试 = 把同一句再打进页面）。
- **停机标记只认"某行行首"或"最后一行"**（`hit_stop_token`，orchestrator.py 与 bridge.py 都有）：避免模型复述规则时误触发。系统提示已要求把 `[DONE]/[NEED_HUMAN]` 单独写在最后一行，且不要用界面的提问组件。
- **tabbit_web_api.py** 把 `Side` 的配置复制一份再用，避免单次请求的 `timeout` 污染基础配置（曾出过 bug）。
- **survey_clicker 的 5 秒延迟**：让扩展先用"问卷出现"判定这一轮结束（`survey` stop reason），否则会退化成等 2 分钟 `stable`。
- Windows 相关：ps1 一律 UTF-8 BOM + CRLF；Python 输出不用 emoji（`survey_clicker` 里只有一个 ✔，如乱码可去掉）。

---

## 8. 常用命令速查（用户机器）

```powershell
# 版本核对
cd G:\tabbit-arena-bridge; python bridge.py --version; python tabbit_web_api.py --version; python survey_clicker.py --version
# AAB：打补丁+打包 → 扩展页“重新加载” → 刷新 Arena 标签页 → 重启服务器
cd G:\arena-agent-bridge; .\.venv\Scripts\Activate.ps1; python G:\tabbit-arena-bridge\aab\patch_aab.py --repo G:\arena-agent-bridge; python -m server
# 冒烟（PowerShell）
$body = @{ model="arena-agent"; messages=@(@{ role="user"; content="请只回复：bridge ok" }); timeout=600 } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://127.0.0.1:8000/v1/chat/completions -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | ConvertTo-Json -Depth 5
# 一键诊断（Send 为什么灰 / 为什么发不出去）；--cancel 取消卡住的请求，--fix 立即点掉问卷
python doctor.py --aab-repo G:\arena-agent-bridge --out doctor.json
# 问卷点击保险（常驻）
python survey_clicker.py --cdp http://127.0.0.1:9223 --target arena.ai/agent
# 抓 Tabbit 结构（先让它回复 PROBE-2468）
python bridge.py probe --cdp http://127.0.0.1:9223 --target web.tabbit.ai/session/f4ece106 --text PROBE-2468 --out probe-tabbit.json
python bridge.py dump  --cdp http://127.0.0.1:9223 --target web.tabbit.ai/session/f4ece106 --out tabbit-1-done.html
python bridge.py dump  --cdp http://127.0.0.1:9223 --target web.tabbit.ai/session/f4ece106 --out tabbit-2-working.html   # 正在输出时
# 全部拉起
.\start-v2.ps1 -AabRepo G:\arena-agent-bridge -Task "……" -MaxRounds 40 -Cooldown 30
```

沙箱侧验证命令（若接手的是同一类带工作区的 AI）：clicker 五种模式 = 起 headless Chromium 打开 `test/survey.html?mode=<m>`，跑 `survey_clicker.py --cdp http://127.0.0.1:9222 --target survey --delay 1 --once`，再 eval `window.__closedBy` 应等于 mode；doctor = `AAB_MOCK_BROWSER=1 AAB_MOCK_DELAY=40 python -m server` 起 mock 服务器，发一条请求后跑 `doctor.py --cdp http://127.0.0.1:9222 --target survey.html --aab-repo <clone> --fix --cancel`。Chromium 需每轮重装 `pip install playwright websocket-client requests; python3 -m playwright install chromium; sudo -n python3 -m playwright install-deps chromium`，二进制在 `~/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`；`/tmp` 与已装 pip 包不跨轮保留；`pkill -f` 会连自己一起杀，用 `pgrep` + `kill`；模拟联调命令见 README-v2 末尾说明。

---

## 9. 待用户提供 / 待确认

1. `doctor.py` 的输出；`survey_clicker.py` 的运行日志（尤其"层级 / 卡片 / HTML"和"第 N 步后消失"）和 Playground 连发两句是否成功。
2. 是否真的加载了带 content.js 补丁的扩展（`Select-String … -Pattern "pre-survey"`）。
3. Tabbit 侧冒烟结果（`tabbit_web_api.py` + 一条 `请只回复：bridge ok`）；**只有失败时**才要 `probe-tabbit.json`、`tabbit-1-done.html`、`tabbit-2-working.html`。
4. 正式任务描述（越具体越不容易打转）。

---

## 10. GitHub 仓库：https://github.com/wpuu/tabbit

用户已于 2026-09-29 建好（建仓时是 **公开** 的——建议改成私有：仓库 Settings → General → 最下方 Danger Zone → Change repository visibility → Make private）。
以后所有改动都推到这里；用户本机用 `git pull` 更新，不再靠下载覆盖。根目录 `push-to-github.ps1` 一键提交并推送。

理由：用户靠"重新下载整个文件夹覆盖"更新，已经两次因本地文件过期踩坑；有仓库后 `git pull` 即可，改动有历史，另一个 AI（包括 Arena Agent 本身，它支持直接开 PR）也能直接在仓库上干活。

- **私有为宜**：整个项目是在自动化 arena.ai（违反其 ToS），且文档里有用户环境细节。提交前已把邮箱、完整会话 UUID 从文档和 `config.json` 里去掉，只留 8 位前缀（`--target` 子串匹配够用）。
- **不要把 ArenaAgentBridge 整个复制进来**：保持"上游 clone + 我们的 `patch_aab.py`"的方式；只在文档里记下上游 commit（`git -C G:\arena-agent-bridge rev-parse --short HEAD`），方便复现。
- **不要提交**：`transcript*.jsonl`、`NEEDS_HUMAN.flag`、`human_inbox.txt`、probe/dump 输出（含对话内容）、`.env`、`uploads/`。根目录已放好 `.gitignore`。
- 首次推送（在工作区根目录，即包含本文的那一层）：`.\push-to-github.ps1`（等价于 `git init -b main; git add .; git commit; git remote add origin https://github.com/wpuu/tabbit.git; git push -u origin main`；Git for Windows 会弹浏览器登录）。
- 以后更新：`git pull`（或再跑一次 `push-to-github.ps1` 提交本地改动），然后照常 `python bridge.py --version` 核对。
- **AI 侧推送**：工作区根目录 `./sync-push.sh "提交说明"`（每次重新 clone 到 /tmp，把工作区同步过去后提交推送；token 读 `.secrets/github_token.txt`，该目录在 .gitignore 里，永远不提交；脚本还会拒绝暂存内容里出现密钥样式的字符串）。用户在本机改了文件并推送后，AI 先跑 `./sync-push.sh --pull` 把远端拷回工作区，再改、再推。`--diff` 只看差异。
- 首次推送已完成：2026-09-30，commit `5b2fc23`，27 个文件。用户本机第一次用 `git clone https://github.com/wpuu/tabbit.git`，以后 `git pull`。
- 用户说 token 在本次对话结束后作废；下次需要新 token 时写入 `.secrets/github_token.txt` 即可。

---

## 11. 时间线摘要

1. 可行性报告（路线 A–E，推荐 API 路线 E）→ 用户明确只要网页互通、Tabbit 对话模式、接受封号风险。
2. v1：`bridge.py` 纯 CDP 双侧（list/probe/dump/run），用户在 Tabbit 9223 上跑通 `list` 和 Arena 的 `probe`（得到 `[data-agent-transcript-message]` 等）。
3. 用户要求先搜现成项目 → 发现 ArenaAgentBridge → 改为 v2；沙箱端到端（mock + 模拟页）通过。
4. 用户装好 AAB，第 1 句成功；卡在"继续工作"问卷 → 中文选择器补丁 → content.js 补丁 → `survey_clicker.py`（待反馈）。
