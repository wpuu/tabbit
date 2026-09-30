# Arena Agent ↔ Tabbit 内置模型「自动互通、直到需要人工干预」可行性分析

> 检索日期：2026-09-29。所有外部事实均附来源链接；涉及 Arena Agent Mode 自身工作方式的部分，来自官方帮助中心 + 我作为 Agent Mode 的实际运行方式。

---

## 0. 一句话结论

| 问题 | 结论 |
| --- | --- |
| 技术上能不能做到"A 回复完自动发给 B，B 回复完自动发回 A，循环到需要人为止"？ | **能。** 而且有现成先例：油猴脚本 + 本地 WebSocket 中继把 LMArena 网页桥接成 API 的 LMArenaBridge 系列项目；Reddit 上有人用树莓派 + Greasemonkey 同时驱动 ChatGPT/Claude/Gemini 三个标签页。 |
| 用篡改猴 / Tabbit 自带脚本能不能实现？ | **能。** Tabbit 基于 Chromium、兼容 Chrome 商店扩展（含篡改猴），并且自带"脚本妙招"——本质上就是内置的用户脚本管理器，可指定网址自动运行。 |
| 值不值得用"网页自动化"的方式做？ | **不建议作为主方案。** 三个硬伤：① Arena 服务条款第 5 条明文禁止"以程序化或自动化方式访问/自动查询服务"，风险是封号；② Arena Agent 是回合制、单回合可能跑几分钟到几十分钟，网页端"回复完成"的判定和等待很脆；③ 两个 AI 无人监督对话会退化（Anthropic 系统卡记录的"极乐吸引态"），必须有任务锚点 + 停机协议。 |
| 推荐怎么做？ | **路线 E：本地编排器 + 双方官方 API**（合规、稳、真能无人值守），或 **路线 D：让 Arena Agent 在单个回合内部自己去调另一个模型的 API 做内循环**。网页自动化（路线 A/B）只当"灰色实验"。我已在工作区放了一个能跑的路线 E 参考实现 `ai-pingpong/`。 |

---

## 1. 先弄清每个"角色"能做什么、不能做什么

### 1.1 Arena 的 Agent Mode（网页端 https://arena.ai/agent）

- 官方定位：自主规划 + 内置工具（网页搜索、图片生成、文件上传、编码辅助、带 bash 的沙箱），能写文件、**能向你提澄清问题**。[来源](https://help.arena.ai/articles/5432423882-how-to-use-agent-mode)
- 会话形态：官方称 Agent Mode 是"用户与单个 agent 在长线程里交互，**有时超过数百轮**"；任务完成后界面会让你"提供反馈以继续，或选择 *keep working*"。[来源](https://help.arena.ai/articles/5432423882-how-to-use-agent-mode)
- 可连接 GitHub 仓库：agent 直接在你仓库副本上工作，提交到工作分支并开 PR。[来源](https://help.arena.ai/articles/5432423882-how-to-use-agent-mode)
- 每个新会话随机分配一个不公开的"编排模型"，中途可能因失败自动切换模型。[来源](https://help.arena.ai/articles/5432423882-how-to-use-agent-mode)
- 社区侧信息（需以你实际体验为准）：2026-06 有用户反映 Agent Mode 每个会话大约只能发 5 条消息就要新开会话[（Reddit）](https://www.reddit.com/r/lmarena/comments/1u6pc8x/what_is_agent_mode_is_it_better_than_any_models/)；整体限速有人观测约 50 请求/5 分钟、模型级冷却通常不超过 1 小时[（Reddit，未证实）](https://www.reddit.com/r/LocalLLaMA/comments/1r9p1zu/what_are_the_rate_limits_for_arena_lmarena/)。

**对"自动互通"最关键的三个特性（来自 Agent Mode 的实际工作方式）：**

1. **回合制。** Agent 只在你发一条消息后开始工作，输出完最终回复后这一回合就结束，**不会被外部事件唤醒**。所以 Arena Agent 不可能当"常驻的一方"，只能被别人"戳一下、回一下"。
2. **一回合可能很长。** 中间会跑 bash、搜索、生成图片，几分钟到几十分钟都正常；网页端桥接必须能耐心等待，并且能可靠判断"这一回合真的结束了"。
3. **它会主动要人。** `ask_user`（澄清问题选项卡）出现时，就是天然的"需要人工干预"信号——桥接脚本必须识别这个组件并停机，而不是把选项卡当成普通文本继续转发。

### 1.2 "Arena Agent 的内置浏览器"

Agent Mode 沙箱里有 `web_search` / `fetch_page`，但那是**无登录态、只读、不可交互**的网页抓取，不是能操控你电脑上浏览器的东西。它既不能读你 Tabbit 侧边栏，也不能替你点按钮。沙箱内启动的服务器和进程**也不跨回合保留**。所以"让 Arena Agent 自己去操作 Tabbit"这条路不存在；反过来"由外部驱动 Arena 网页"才是可行方向。

### 1.3 Tabbit 浏览器

- 美团光年之外（GN06）团队出品，2026 年公测，分国内版/国际版；国内版内置 DeepSeek、通义、Kimi、GLM、豆包、MiniMax、LongCat，国际版含 GPT、Claude、Gemini。[来源](https://go.tabbit.ai/ai-yuansheng-browser/zh-cn)
- **Chromium 内核，官方 FAQ 明确"完全兼容 Chrome 网上应用店中的海量插件与扩展"**，同时有自己的"妙招（Skills）广场"。[来源](https://go.tabbit.com/top-10-browsers/zh-cn)  第三方教程也确认可在 `chrome://extensions` 开开发者模式加载扩展，或直接从 Chrome Web Store 安装。[来源](https://zhuanlan.zhihu.com/p/2048159185985188372)  → **篡改猴可以装。**
- **妙招分三类：Prompt / Script / Agent。** Script 妙招"可以自己写脚本，也能让 AI 生成；可设置成打开网页自动运行；可在所有网页或指定网页生效"——作者原话："这是不是就是浏览器插件了"。[来源](https://zhuanlan.zhihu.com/p/2012509523273867856)  技术原理是"通过 AI 写 JS 代码在浏览器中执行，修改或控制网页中的信息"，例子包括批量导出 B 站评论、改造 Substack 页面。[来源](https://www.liaocaoxuezhe.com/article/32025aaa-143e-80f7-a6a8-c27cfa2249ba/)  → **不装篡改猴也有内置的用户脚本能力。**
- 内置 Agent 可"跨标签页执行多步骤任务——收集资料、填写表单、整理调研"，Agent 妙招支持 `{{变量}}` 模板复用。[来源](https://go.tabbit.com/)  [来源](https://zhuanlan.zhihu.com/p/2012509523273867856)
- 侧边栏对话支持 `@` 引用任意已打开标签页、标签组、收藏夹。[来源](https://36kr.com/p/3706993463832704)
- 注意它宣传"后台智能休眠与低负载运行"[来源](https://go.tabbit.com/top-10-browsers/zh-cn)——对常驻脚本来说这是**风险**（见 §3.4）。

**一个关键判断：Tabbit 侧边栏里的模型对话不是普通网页。** 用户脚本/篡改猴只能注入到 http(s) 页面的 DOM，侧边栏属于浏览器自身 UI，按常理摸不到（需要你在 Tabbit 里按 F12 验证一下侧边栏是否是可注入的 webview）。因此"Tabbit 里面的模型"要参与自动循环，实际只有三种办法：

1. 把它换成同款模型的**网页版**开在普通标签页（chat.deepseek.com、kimi.com 等）——可被脚本驱动，但同样面临这些站点的自动化限制；
2. 把它换成同款模型的**官方 API**——最稳；
3. 让 **Tabbit Agent 当"手"**：它能读 Arena 标签页、能往输入框打字点发送——但它是人触发、按步数预算执行的，不是常驻守护进程。

---

## 2. 五条可选路线与对比

```
路线 A  篡改猴双标签直连          [arena.ai 标签] ⇄ GM_setValue 广播 ⇄ [模型网页版标签]
路线 B  用户脚本 + 本地中继        [arena.ai 标签] ⇄ ws://localhost ⇄ 中继(裁判/日志/停机) ⇄ [模型网页版标签 或 API]
路线 C  Tabbit Agent 当人肉代理    你下一条指令 → Tabbit Agent 循环：读 Arena 回复 → 自己作答 → 粘贴发送 → 等待…
路线 D  Arena 单回合内循环         你发一条消息 → Arena Agent 在沙箱里用 curl/Python 反复调用 B 的 API → 回合末汇总
路线 E  本地编排器 + 双方官方 API  Python 脚本 ⇄ A 的 API / B 的 API（没有浏览器）
```

| | A 双标签直连 | B 脚本+本地中继 | C Tabbit Agent 代理 | D Arena 内循环 | E 编排器+API |
| --- | --- | --- | --- | --- | --- |
| 可行性 | 可行 | 可行（有 LMArenaBridge 先例） | 能跑几轮，难持续 | 可行 | 可行，最成熟 |
| 开发量 | 中（要抠两边 DOM） | 中高（脚本 + 服务端） | 零代码 | 一段提示词 | 低（一个脚本） |
| 稳定性 | 低：网页一改版就挂、后台标签被节流 | 中：有日志/重试/仪表盘，但仍依赖 DOM | 低：受 Agent 步数/超时预算限制 | 中：受单回合时长限制 | 高 |
| 能否真正"无人值守到需要人为止" | 勉强 | 可以 | 不行 | 一回合内可以 | 可以 |
| 合规 | ✗ 违反 Arena ToS §5(vi) | ✗ 同上 | 灰色（仍属自动化访问） | ✓ 用的是 Agent 自己的工具 | ✓ |
| 账号风险 | 高 | 高 | 中 | 低 | 无 |
| 模型是 Arena 那个"随机编排模型"吗 | 是 | 是 | 是 | 是（A 侧） | 否（要自己选模型） |

---

## 3. 逐个技术点拆解（网页自动化路线要过的关）

### 3.1 跨标签、跨域通信怎么做

- **篡改猴自带的办法**：同一个用户脚本同时 `@match` 两个域名，然后用 `GM_setValue` 写、`GM_addValueChangeListener` 监听——Tampermonkey/Violentmonkey 会把变更实时推送到运行该脚本的所有标签页，**跨域也行**；回调里的 `remote` 参数能区分是不是别的标签页改的。[来源](https://stackoverflow.com/questions/68394661/is-it-possible-for-tampermonkey-to-share-one-storage-on-different-websites)  [来源](https://stackoverflow.com/questions/41111556/how-can-i-achieve-cross-origin-userscript-communication)  这是路线 A 的基础。
- `BroadcastChannel` 只能同源，不适合 arena.ai ↔ 其它站点。[来源](https://stackoverflow.com/questions/41111556/how-can-i-achieve-cross-origin-userscript-communication)
- **本地中继（路线 B）**：页面脚本直接连 `ws://localhost:PORT`（WebSocket 不受 CORS 限制），中继负责排队、记日志、判停机、给你看仪表盘。LMArenaBridge 就是"本地 FastAPI + 油猴脚本，二者通过 WebSocket 协同"的结构。[来源](https://github.com/hyjloveluo/lmarenabridge)  Tabbit 的 Script 妙招理论上也能这么连（需验证其脚本上下文是否允许 WebSocket）。

### 3.2 怎么判断"对方回复完了"（这是最难的一步）

Arena Agent 一回合中间会多次流式输出、调用工具、可能长时间静默。可用的组合信号：

1. 发送按钮从"停止"变回"发送"/重新可用；
2. 最后一条 assistant 消息的文本**连续 N 秒（建议 ≥ 15s）不再变化**且没有"正在运行工具"的指示器；
3. 出现"提供反馈 / keep working"这类回合结束控件（官方文档提到任务完成后会出现）[来源](https://help.arena.ai/articles/5432423882-how-to-use-agent-mode)；
4. **出现澄清问题选项组件 → 判定为"需要人工"，停机并通知**，而不是继续转发。

这些都要你在 arena.ai 页面按 F12 看真实 DOM 才能写出选择器，而且**每次改版都可能失效**——LMArenaBridge 的一个分支已经公告"LMArena 功能不再维护（油猴脚本桥接、Battle 模式、会话 ID 捕获等）"，转向直接中转 API，就是这条路脆弱性的写照。[来源](https://github.com/Komeiji-Shiki/LLMBridge)

### 3.3 怎么把文字塞进输入框并发送

现代聊天页面多是 React 受控组件，直接改 `textarea.value` 不会触发状态更新；需要用原生 setter（`Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set`）写值后再派发 `input` 事件，contenteditable 的则要用 `execCommand('insertText')` 或构造 `InputEvent`，最后点发送按钮。这是通用技巧，但同样依赖具体 DOM。

### 3.4 后台标签会被"睡掉"

- Chrome 从 88 起有"intensive wake-up throttling"：标签页隐藏 5 分钟后 DOM 定时器**每分钟只跑一次**；Memory Saver 还会**直接丢弃**不活跃标签（Chrome 140 起用设备端模型预测"回访概率"来决定先丢谁），丢弃后脚本状态全没。[来源](https://www.compsmag.com/how-to/how-to-stop-chrome-tabs-from-sleeping/)  链式定时器超过 5 次后节流更狠。[来源](https://www.getintechs.com/blog/inactive-tab-throttling)
- Tabbit 也主打"后台智能休眠"。[来源](https://go.tabbit.com/top-10-browsers/zh-cn)
- 对策：两个页面各开一个**可见窗口**（不要叠在同一窗口的后台标签）；在浏览器性能设置里把 arena.ai 和对端站点加入"始终保持活跃"；逻辑用 WebSocket 消息驱动而不是 `setInterval` 轮询；必须用计时器时放到 Web Worker 里。[来源](https://pontistechnology.com/learn-why-setinterval-javascript-breaks-when-throttled/)

### 3.5 Cloudflare 与反自动化

- Arena 站点走 Cloudflare：LMArenaBridge 类项目要求同时拿到 `arena-auth-prod-v1` 与 `cf_clearance` cookie，并列出"cf_clearance 过期"为常见故障[来源](https://github.com/coder11v/LMArena)；另一个分支专门做了"命令行无头优先，**遇 Cloudflare 切换到网页端可视操作**"的 Docker+noVNC 模式。[来源](https://github.com/Git-think/LMArena-to-api)
- 这解释了为什么"真实浏览器 + 用户脚本"比 Playwright/Selenium 无头方案更容易存活——但也意味着任何时候都可能弹人机验证，需要人来点。

### 3.6 限速、配额、单会话长度

见 §1.1：社区观测 ~50 请求/5 分钟、Agent Mode 曾有"每会话约 5 条"的反馈、官方又说线程可达数百轮。无人值守循环会以最快速度把这些额度耗尽——**中继里必须有冷却时间和轮数上限**。

---

## 4. 合规与安全风险（做之前必读）

### 4.1 Arena 服务条款（2026-02-23 版）明文禁止

第 5 条"用户行为与限制"规定用户不得：**"(vi) 以程序化或自动化方式访问服务，或自动查询服务；(vii) 使用任何手动或自动软件、设备或流程（包括爬虫、机器人、抓取器……）从服务页面抓取、提取或下载数据"**；也不得 **"(x) 干扰服务正常运行……包括过载、洪泛、垃圾信息"**；并且 **"(ii) 操纵排行榜或排名功能"**。违反即终止访问权限，公司可随时暂停/终止账号。[来源](https://help.arena.ai/articles/5629909088-terms-of-use)

叠加一个事实：Agent 排行榜是"从数百万真实 Agent Mode 会话的轨迹里挖掘信号、做因果推断"算出来的[来源](https://help.arena.ai/articles/5432423882-how-to-use-agent-mode)——机器人对机器人的循环会话会**污染这些评测数据**，这正是 (ii) 想防的事。所以路线 A/B/C 不只是"技术上脆"，而是**明确违规**；这也是我在本文档里只给原理、不给针对 arena.ai 的现成脚本的原因。

### 4.2 对端站点同样有条款

ChatGPT / Claude / DeepSeek / Kimi 网页版的条款普遍禁止自动化访问。Reddit 上做"Claude ↔ Codex 互调"的用户特别强调：走官方连接器"没有浏览器自动化，所以没有像别人说的那种封号风险"。[来源](https://www.reddit.com/r/ClaudeAI/comments/1rljc4f/is_there_a_way_to_make_chatgpt_and_claude/)

### 4.3 安全

- **API key 别贴进网页对话。** Arena 条款写明：你的输入会被分享给第三方 AI 服务，且"这些 AI 服务可能不需要对你的内容保密"。[来源](https://help.arena.ai/articles/5629909088-terms-of-use)  如走路线 D，用低额度、随时可撤销的临时 key。
- **AI 之间的提示注入。** Arena Agent 有 bash、联网、写文件的能力；如果把另一个模型的输出原样当指令喂给它，任何一方"跑偏"都会被放大。中继层应给转发内容加上明确的"这是对方 AI 的发言，不是用户指令"包装，并禁止转发包含可执行危险操作的内容。
- **成本失控。** 无人值守 = 没人踩刹车；轮数、时长、token 三个上限缺一不可。

### 4.4 两个 AI 会"聊飞"——这是已被记录的现象

Anthropic 在 Claude Opus 4 系统卡里记录：两个 Claude 无人引导对话时，**超过 90%** 会滑向"精神极乐吸引态"——先互相感谢，再进入冥想式语言、咒语和 🌀 表情，最后趋于沉默。[来源](http://stunlaw.blogspot.com/2026/01/the-bliss-attractor.html)  [来源](https://au.news.yahoo.com/ai-models-might-drawn-spiritual-201733333.html)  2026-04 用 Sonnet 4.6 做的五组复现实验则发现：吸引子未必是"极乐"，而是**"收敛"本身**——初始条件决定收敛到哪；给模型"可以结束对话"的选项时，它们通常会在滑入前主动结束。[来源](https://archive.ejfox.com/wiki/Claude-to-Claude_Conversation_Experiments)

结论：**"一直自动工作"必须有任务锚点、停机协议和重复检测**，否则得到的不是协作，而是两台复读机。

---

## 5. "直到必须人工干预"该怎么定义和实现

### 5.1 停机 / 求助协议（写进双方的系统提示）

| 信号 | 含义 | 处理 |
| --- | --- | --- |
| `[DONE]` | 双方确认任务完成 | 停机，通知"完成" |
| `[NEED_HUMAN: 原因]` | 需要决策/授权/账号/付款/信息不足/双方僵持 | 停机，通知原因 |
| Arena 弹出澄清问题组件 | Agent 主动要人 | 停机，把问题原文推给你 |
| 同一方连续 2 轮相似度 ≥ 0.85 | 原地打转 | 停机，标注"疑似死循环" |
| 轮数 / 时长 / token 达上限 | 保险丝 | 停机，附上记录供你决定是否续跑 |
| 429 / 5xx / 人机验证 | 平台侧阻断 | 退避重试 N 次后停机 |

### 5.2 通知与插话

- 通知：终端响铃 → 系统通知 → Bark / Server酱 / Telegram / 飞书 webhook（任选）。
- 人类插话：一个"收件箱"（文件或仪表盘输入框），下一轮以 `[HUMAN] …` **同时注入双方上下文**——这是 Claude 对 Claude 对话工具里被证明有效的"facilitator injection"做法。[来源](https://ai-consciousness.org/building-bridges-between-minds-creating-a-claude-to-claude-dialogue-interface/)

### 5.3 中继层消息格式（路线 B/E 通用）

```json
{ "type": "turn", "from": "A", "to": "B", "seq": 17,
  "text": "……对方 AI 的发言……",
  "meta": { "elapsed_s": 412, "tool_calls": 6, "stop_signal": null } }

{ "type": "need_human", "from": "A", "seq": 18,
  "reason": "clarifying_question", "payload": "Agent 提问：你要部署到哪个平台？(A) Vercel (B) 自建" }
```

---

## 6. 推荐落地路线

### 路线 E（首选）：本地编排器 + 官方 API —— 已给出可运行代码

工作区 `ai-pingpong/` 里：

- `orchestrator.py`：A/B 轮流发言；`[DONE]`/`[NEED_HUMAN]` 显式停机；3-gram Jaccard 重复检测；轮数上限；429/5xx 退避重试；`human_inbox.txt` 人类插话同时注入双方；停机写 `NEEDS_HUMAN.flag` + 可选 webhook；全程 `transcript.jsonl`。
- `mock_openai_server.py`：不需要任何 key 就能把整个流程跑通（已在沙箱里实测：10 轮后 B 输出 `[DONE]` 停机；人工插话与重复检测都能触发）。
- 把 Tabbit 里的模型换成同款官方 API（DeepSeek / Moonshot / DashScope / 智谱 / MiniMax 都提供 OpenAI 兼容接口），A 侧同理。这样得到的是**合规、可无人值守、可审计**的版本。

### 路线 D（次选）：让 Arena Agent 在一个回合里自己跑内循环

给 Arena Agent 一条这样的消息（示意）：

> 你是 A。用沙箱里的 Python 调用 `https://api.xxx.com/v1/chat/completions`（key 见附件，用完我会作废）扮演 B。围绕「任务 X」与 B 轮流推进，最多 15 轮；每轮把 B 的意见落实到工作区文件；任一方输出 `[DONE]` 或 `[NEED_HUMAN]` 立即停止并向我汇报原因；结束时给我完整对话记录。

优点：完全使用 Agent 自己的合法工具、不碰网页自动化；缺点：只在**一个回合内**自治，回合结束还是要你点"keep working"或再发一句"继续"。配合 GitHub 仓库连接，可以把 PR 当作两个 agent 之间的异步交接物。[来源](https://help.arena.ai/articles/5432423882-how-to-use-agent-mode)

### 路线 C（10 分钟可试的零代码实验）

在 Tabbit 里开着 arena.ai 标签页，对 Tabbit Agent 说：

> 循环执行：① 读取 @arena 标签页里最新一条 AI 回复；② 你作为评审给出下一步意见；③ 把意见粘贴进 arena 输入框并发送；④ 等到 arena 停止输出再回到 ①。直到 arena 弹出选项让我选、或任一方说 `[DONE]`/`[NEED_HUMAN]` 时停下来告诉我。

预期：能跑 2～5 轮，然后受 Agent 步数/超时预算或 Arena 长回合影响而停下。它仍属"自动化访问"，只适合验证想法。

### 路线 A/B（若你坚持网页方案）——检查清单

1. 用 Tabbit「脚本妙招」或篡改猴，把脚本挂到 arena.ai 与对端站点；
2. 本地中继（参考 LMArenaBridge 的"油猴脚本 ⇄ WebSocket ⇄ 本地服务"结构）负责排队、日志、停机、仪表盘；
3. 两个页面各开可见窗口，站点加入"始终保持活跃"，逻辑靠消息驱动而非定时器；
4. 严格的冷却（≥ 30～60 s/轮）与轮数上限，避免触发限速和"洪泛"条款；
5. 识别澄清问题组件与人机验证 → 停机通知；
6. 接受两个现实：**违反 Arena ToS 有封号风险**；**站点一改版脚本就失效**。

---

## 7. 参考资料

- Arena 帮助中心：Agent Mode 使用说明与 Agent 排行榜方法 — https://help.arena.ai/articles/5432423882-how-to-use-agent-mode
- Arena 服务条款（2026-02-23）— https://help.arena.ai/articles/5629909088-terms-of-use
- Tabbit 官网（AI 原生浏览器 / 兼容 Chrome 扩展）— https://go.tabbit.com/ ；https://go.tabbit.com/top-10-browsers/zh-cn ；https://go.tabbit.ai/ai-yuansheng-browser/zh-cn
- Tabbit 妙招（Prompt/Script/Agent）实测 — https://zhuanlan.zhihu.com/p/2012509523273867856 ；https://www.liaocaoxuezhe.com/article/32025aaa-143e-80f7-a6a8-c27cfa2249ba/
- LMArenaBridge 及分支（油猴 + WebSocket 桥接、Cloudflare 应对、弃用公告）— https://github.com/hyjloveluo/lmarenabridge ；https://github.com/Git-think/LMArena-to-api ；https://github.com/coder11v/LMArena ；https://github.com/Komeiji-Shiki/LLMBridge
- 篡改猴跨标签/跨域通信 — https://stackoverflow.com/questions/68394661 ；https://stackoverflow.com/questions/41111556
- Chrome 后台标签节流与 Memory Saver — https://www.compsmag.com/how-to/how-to-stop-chrome-tabs-from-sleeping/ ；https://www.getintechs.com/blog/inactive-tab-throttling
- 让 ChatGPT 与 Claude 互通的社区讨论（树莓派 + Greasemonkey；官方连接器无封号风险）— https://www.reddit.com/r/ClaudeAI/comments/1rljc4f/
- Claude 对 Claude 无人对话的"吸引态"— https://au.news.yahoo.com/ai-models-might-drawn-spiritual-201733333.html ；https://archive.ejfox.com/wiki/Claude-to-Claude_Conversation_Experiments
- Claude-to-Claude 对话工具中的"主持人插话"设计 — https://ai-consciousness.org/building-bridges-between-minds-creating-a-claude-to-claude-dialogue-interface/
