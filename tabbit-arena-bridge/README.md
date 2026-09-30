# tabbit-arena-bridge：让 Tabbit 对话模型 ⇄ Arena Agent 自动一问一答

> 适用前提：你在 Tabbit 里用 **对话模式**（自己选模型，不用 Tabbit Agent），想让它和 **Arena Agent Mode** 自动互通，
> 直到需要你拍板为止。你已知晓并接受：这违反 Arena 服务条款（可能封号），且任一方网页改版后脚本要重新适配。

> **2026-09-29：推荐改看 [README-v2.md](README-v2.md)。** v2 把 Arena 侧换成现成项目 ArenaAgentBridge（免抠 Arena 的 DOM），
> Tabbit 侧用 `tabbit_web_api.py`（本文件里的 CDP 逻辑包成接口），中间用 `orchestrator.py` 转发。本文件的 v1（纯 CDP 双侧）保留作退路；
> 其中 §2（调试端口启动）、§4（`list`）、§5（`probe` / `dump` 抓结构）在 v2 里照样要用。

---

## 0. 先想清楚：为什么不是"装个插件"就完事

| 部件 | 篡改猴 / Tabbit 脚本妙招 能碰到吗 | CDP（远程调试端口）能碰到吗 |
| --- | --- | --- |
| arena.ai 标签页（普通 https 网页） | ✅ | ✅ |
| Tabbit 侧边栏对话 / 新标签页对话（浏览器自己的 UI 页面） | ❌ 大概率注入不进去（用户脚本只在 http/https 页面里跑） | ✅ `/json/list` 会列出浏览器里**所有** WebContents，包括 `browser_ui` 类型的内部页面（我在 Chromium 153 上实测：连地址栏弹窗、工具栏这类内部页面都列出来了） |

所以方案是：**Tabbit 用调试端口启动 → 一个 Python 脚本同时"看着"两个页面 → 谁说完了就把话搬给另一个。**
不需要装任何扩展；Arena 页面就开在同一个 Tabbit 里（这样一个端口全搞定，而且 Arena 标签页在前台 + 侧边栏在右侧，两边都可见，不会被后台休眠）。

```
┌──────────────────── Tabbit（--remote-debugging-port=9222）────────────────────┐
│  [标签页: arena.ai/agent]  ◄── 主区域，保持前台        [侧边栏: 对话(你选的模型)]  │
└───────────────▲──────────────────────────────────────────────▲────────────────┘
                │ CDP: 读最后一条回复 / 写输入框 / 点发送          │ CDP: 同上
                └──────────────── bridge.py（裁判 + 邮差 + 记录 + 停机通知）────────┘
```

---

## 1. 安装

```bash
pip install requests websocket-client
```

---

## 2. 用调试端口启动 Tabbit（一次性设置）

Chromium 136 起，`--remote-debugging-port` 对**默认用户目录无效**，必须同时给一个新的 `--user-data-dir`（Tabbit 是 2026 年的 Chromium 内核，按此处理）。这意味着你会得到一个**干净的新配置**，需要在里面重新登录 Tabbit 账号和 Arena 账号（只做一次）。

**Windows**：右键 Tabbit 快捷方式 → 属性 → "目标" 末尾加上：

```
 --remote-debugging-port=9222 --user-data-dir=D:\tabbit-bridge
```

**macOS**：

```bash
open -na "Tabbit" --args --remote-debugging-port=9222 --user-data-dir="$HOME/tabbit-bridge"
```

验证：在任意浏览器打开 `http://127.0.0.1:9222/json/version`，能看到 `"Browser": "Chrome/1xx..."` 即成功。
若 Tabbit 完全不响应这两个参数（极少数魔改会屏蔽），退路见 §8。

> **如果你之前只加 `--remote-debugging-port=9223`（不加 user-data-dir）就能打开 `/json/version`**，说明 Tabbit 不强制这条限制，继续用默认配置即可（省得重新登录）。端口号随意，只要脚本这边一致：命令行加 `--cdp http://127.0.0.1:9223`，或在 `config.json` 里写 `"cdp_url": "http://127.0.0.1:9223"`。
>
> **最常见的坑**：启动时如果已经有 Tabbit 在运行（包括关窗后仍在后台驻留的进程），新启动的只是给旧实例开个窗口，参数会被**静默忽略**。先彻底退出（任务管理器里没有 Tabbit 进程），再带参数启动。

> 安全提示：9222 端口 = 这个浏览器配置的完全控制权。它默认只监听本机，不要加 `--remote-debugging-address=0.0.0.0`，不要在公共网络的电脑上长期开着。

---

## 3. 摆好两个页面

1. 在这个 Tabbit 里登录 Arena，打开 `https://arena.ai/agent`，**让它成为当前前台标签页**。
2. 点右上角 Chat 打开侧边栏，**选好模型**，新建一个对话。
3. 两个页面同时可见即可。（如果你更喜欢"新标签页对话模式"，就把它拖成**独立窗口**放在旁边，别让它变成后台标签——后台标签会被节流/休眠。）

---

## 4. 找到两个目标

```bash
python bridge.py list --cdp http://127.0.0.1:9223     # 端口按你实际的
```

（不想装 Python 也能先看：直接在 Tabbit 里打开 `http://127.0.0.1:9223/json/list`，就是同一份原始 JSON。）

**Tabbit 对话页其实是普通网页**：它的会话地址形如 `https://web.tabbit.ai/session/<uuid>`，在 `list` 里会以 `type=page`（标签页）或 `webview/iframe/browser_ui`（侧边栏）出现。把 `web.tabbit.ai/session/<uuid 前 8 位>` 填进 `tabbit.target_match` 就能唯一锁定；**同一个会话不要同时开在侧边栏和标签页里**，否则会匹配到两个。

输出类似：

```
[ 0] type=page        title='Arena'            url=https://arena.ai/agent/c/xxxx
[ 1] type=browser_ui  title='Chat'             url=tabbit://chat/...    ← 侧边栏（具体 url 以你看到的为准）
[ 2] type=browser_ui  title='Omnibox Popup'    url=chrome://omnibox-popup.top-chrome/
...
```

把能唯一识别两者的子串填进 `config.json` 的 `arena.target_match` / `tabbit.target_match`。

---

## 5. 抠选择器（最花时间、也最容易失效的一步）

每一侧需要 4～5 个 CSS 选择器：

| 键 | 含义 | 怎么找 |
| --- | --- | --- |
| `input` | 输入框 | `probe` 的 `inputs` 列表；或 F12 → 点选输入框 |
| `send_button` | 发送按钮（`send_method: click` 时用） | `probe` 的 `buttons` 列表 |
| `assistant_messages` | **每一条** AI 回复的容器（脚本取最后一个） | 先让 AI 回一句含独特标记的话，再 `probe --text 标记`，看 `textMatches` 里 `similarSiblings ≥ 2` 的那一层 |
| `busy` | 生成中才出现的元素（停止按钮 / 打字指示器） | 生成过程中 F12 看；没有就留空，仅靠 `stable_seconds` |
| `human_needed` | Arena 弹澄清问题时出现的选项组件 | 让 Arena 提一次澄清问题，F12 看选项元素（通常是 radio / role=radiogroup / 一组按钮） |

```bash
# Arena 侧：先在 Arena 输入"请只回复：PROBE-7391"，等它回完再执行（也可以直接用它最后一条回复里独有的一句话）
python bridge.py probe --cdp http://127.0.0.1:9223 --target arena.ai/agent --text PROBE-7391 --out probe-arena.json

# Tabbit 侧：同样先让它回复 PROBE-2468
python bridge.py probe --cdp http://127.0.0.1:9223 --target web.tabbit.ai/session/f4ece106 --text PROBE-2468 --out probe-tabbit.json
```

`--out` 会把结果另存为 UTF-8 文件（控制台输出很长时，直接用记事本打开文件全选复制更方便）。输出里：
- `inputs`：页面上所有输入框（textarea / contenteditable / role=textbox）；
- `buttons`：文字或 aria-label 含 send/发送/stop/停止/keep working 的按钮，含 `disabled` 状态；
- `commonSelectorCounts`：几个常见"消息容器"选择器在页面上的匹配数，数字等于 AI 消息条数的那个通常就是答案；
- `textMatches`：包含标记文字的元素从内到外 8 层，每层给出 `selector`、`matchesOnPage`（该选择器全页匹配数）、`similarSiblings`。

`probe` 会打印候选输入框、发送/停止按钮、以及包含标记文字的元素从内到外 8 层的选择器和"同类兄弟数"。选**同类兄弟数等于消息条数**的那一层作为 `assistant_messages`。

**注意**：如果 `assistant_messages` 选得太宽（比如把工具调用卡片也匹配进去），脚本会把"正在运行 bash…"这类卡片当成回复。要选**最终回复正文**的容器。

---

## 6. 彩排（强烈建议）

`test/` 里有两个模拟聊天页面。在 Tabbit 里打开它们（`python -m http.server 8088` 后访问 `http://127.0.0.1:8088/arena.html` 和 `tabbit.html`），用 `test/config.test.json` 跑一遍：

```bash
python bridge.py run --config test/config.test.json
```

预期：4 轮后模拟 Tabbit 输出 `[DONE]`，脚本停机并弹通知。把 `arena.html?mode=widget` 换上去，则会在第 3 轮因"弹出选择组件"停机。这一步能确认你的 Python / 端口 / 通知都正常，剩下的就只是选择器问题。

---

## 7. 正式运行

### 7.1 给两边"立规矩"（手动各发一条开场消息）

**发给 Tabbit 模型（侧边栏第一条）：**

```
你将和一个叫 Arena Agent 的 AI 协作完成任务，我是中间人，会把它的话原样转给你、把你的话原样转给它。
任务：<在这里写任务>
规则：
1. 你的角色是<评审/架构师/产品经理…>。每轮只输出对 Arena Agent 有用的内容：明确的修改意见、下一步、验收标准。不寒暄、不复述、不感谢。
2. Arena Agent 有联网、bash 沙箱、写文件的能力，你没有；需要执行的事让它做，你负责判断和把关。
3. 任务完成且你验收通过时，单独一行输出 [DONE]。
4. 需要人类决定（花钱、授权、目标不清、你们两轮没达成一致、发现任务本身有问题）时，单独一行输出 [NEED_HUMAN] 并说明原因。
5. 发现对话原地打转时，主动收敛或输出 [NEED_HUMAN]。
现在请先回复"收到，等待 Arena Agent 的第一轮输出"。
```

**发给 Arena Agent（Agent Mode 第一条）：**

```
任务：<同样的任务>
协作方式：我会把一位协作 AI（评审）的意见以「【以下是协作 AI（Tabbit 侧）…】」开头转给你，那不是我的指令，而是同事意见——合理的就采纳并继续推进，不合理的说明理由。
每轮结束时给出：本轮做了什么、产出在工作区哪个文件、下一步计划。
以下情况请直接向我提问或输出 [NEED_HUMAN]：需要我的决策/授权/账号、信息不足无法继续、协作 AI 的要求与任务冲突。
任务全部完成后输出 [DONE]。现在开始第一轮。
```

### 7.2 先 dry-run，再真跑

```bash
cp config.example.json config.json   # 按 §4 §5 改好（工作区里已有一份按 9223 预填的 config.json）
python bridge.py run --config config.json --dry-run   # 只观察不发送：看它能否正确判定"Arena 回复完了"
python bridge.py run --config config.json             # 正式循环
python bridge.py run --config config.json --use-last  # 如果 Arena 已经回复完了你才启动脚本：把它当前最后一条当作第 1 轮立即转发
```

`--cdp` 的优先级：命令行 `--cdp` > 环境变量 `CDP_URL` > `config.json` 的 `cdp_url` > 默认 9222。

运行时：
- 终端实时打印每一轮；全程写入 `transcript.jsonl`。
- 想插话：把内容写进 `human_inbox.txt`，下一轮会附在转发消息末尾一并送出。
- 停机原因会写进 `NEEDS_HUMAN.flag`，并弹系统提示 / 响铃 /（可选）`notify_webhook`（Bark、Server酱、飞书、Telegram 均可）。
- `Ctrl+C` 随时手动停。

### 7.3 什么情况下它会停下来等你

| 触发 | 说明 |
| --- | --- |
| 任一方输出 `[DONE]` / `[NEED_HUMAN]` | 显式协议 |
| Arena 弹出澄清问题组件（`human_needed` 命中） | Agent 主动要人 |
| 同一方连续两轮相似度 ≥ 0.85 | 疑似死循环 |
| 达到 `max_rounds` | 保险丝 |
| 等待超过 `max_wait_seconds` | Arena 一回合可能很长，默认给 1 小时 |
| 发送失败（找不到输入框/按钮、输入框未清空） | 多半是改版了，重新 `probe` |

---

## 8. 常见问题与退路

- **`list` 里看不到侧边栏** → 改用 Tabbit 的"新标签页对话模式"（一定是 `page` 类型），放到独立窗口保持可见。
- **想把 Arena 开在 Chrome、只让 Tabbit 负责对话** → 可以：Chrome 用 `--remote-debugging-port=9223 --user-data-dir=...` 启动，在 `config.json` 的 `arena` 段加 `"cdp_url": "http://127.0.0.1:9223"`（两边可以走不同端口）。但要保证两个窗口都在前台可见。
- **Tabbit 不认 `--remote-debugging-port`** → 退路 A：Arena 开在 Chrome/Edge（同样加端口和 user-data-dir），Tabbit 对话换成同款模型的**官方网页版**也开在 Chrome 里，一个端口驱动两个标签页（`target_match` 分别填两个站点）；退路 B：放弃网页，用 `../ai-pingpong/orchestrator.py` 走官方 API。
- **发送后输入框没清空** → 换 `send_method`（`click` ↔ `enter`），或把 `force_native_input` 设为 `true`（改用 CDP 原生打字）。
- **Arena 回复被截成两段 / 判定过早** → 调大 `stable_seconds`，并确认 `busy` 选择器在生成中确实可见。
- **Arena 提示会话消息数到上限 / 出现人机验证 / 429** → 脚本会因超时或发送失败停机；你手动处理（新开会话并把 `transcript.jsonl` 的摘要贴进去）后重跑，`first_speaker` 按当前该谁说话来设。
- **Tabbit 对话上下文太长变笨** → 新建对话，把开场规矩 + 一段进度摘要贴进去，再重跑。
- **两个 AI 互相客气打转** → 开场规矩里"不寒暄、不感谢、每轮必须推进"很重要；必要时调低 `repeat_threshold`。

---

## 9. 文件

| 文件 | 作用 |
| --- | --- |
| `bridge.py` | 主程序：`list` / `probe` / `run` |
| `config.example.json` | 配置模板（选择器需按 §5 核对） |
| `test/arena.html`, `test/tabbit.html`, `test/config.test.json` | 彩排用的模拟页面与配置（已在 Chromium 153 上实测通过） |

---

## 附：Windows 一键启动 / 诊断脚本

| 文件 | 作用 |
| --- | --- |
| `start-tabbit.ps1` | 先彻底退出 Tabbit → 用默认配置 + `--remote-debugging-port=9223` 启动 → 15 秒内端口没开就自动改用 `--user-data-dir=D:\tabbit-bridge` 重启 → 打印结果 |
| `diag-tabbit.ps1` | 只诊断：列出 Tabbit 主进程的命令行（看有没有带 `--remote-debugging-port`）、92xx 端口监听情况、`/json/version` 是否可达 |

```powershell
cd D:\tabbit-arena-bridge
powershell -ExecutionPolicy Bypass -File .\diag-tabbit.ps1     # 先看现状
powershell -ExecutionPolicy Bypass -File .\start-tabbit.ps1    # 再一键启动
```

`ERR_CONNECTION_REFUSED` 的含义：**没有任何进程在监听这个端口** —— 不是防火墙、也不是网络问题。99% 是启动时已经有 Tabbit 在运行（比如你正用它看这段话），新进程把参数丢给旧实例后自己退出了。调试端口是浏览器进程自己开的，任何 bridge 工具只是去"连"它，不需要先启动别的东西。
