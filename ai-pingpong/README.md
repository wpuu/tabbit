# ai-pingpong —— 两个 AI 自动互通（API 版参考实现）

这是《Arena Agent ↔ Tabbit 模型自动互通可行性分析》里推荐的 **路线 E** 的最小可运行示例：
本地脚本当"裁判 + 邮差"，A 的回复自动转给 B，B 的回复自动转给 A，直到触发"必须人工干预"的停机条件。

不做任何网页自动化，只走官方 / OpenAI 兼容 API，所以不踩 Arena 或其它平台的 ToS。

## 文件

| 文件 | 作用 |
| --- | --- |
| `orchestrator.py` | 编排器：轮流调用 A、B，记录 `transcript.jsonl`，停机时写 `NEEDS_HUMAN.flag` 并可 POST webhook |
| `mock_openai_server.py` | 假的 OpenAI 兼容服务器，不需要任何 key 就能把流程跑通 |

## 30 秒跑通（无需 key）

```bash
pip install requests
python3 mock_openai_server.py &
A_BASE_URL=http://127.0.0.1:8123/v1 A_API_KEY=x A_MODEL=mock-a A_NAME=Arena-Agent \
B_BASE_URL=http://127.0.0.1:8123/v1 B_API_KEY=x B_MODEL=mock-b B_NAME=Tabbit-Model \
python3 orchestrator.py --task "一起把一个 Python 排序函数写出来并互相 review" --max-rounds 12
```

## 换成真实模型

任何 OpenAI 兼容接口都行（DeepSeek、Kimi/Moonshot、通义千问、智谱、MiniMax、OpenAI、OpenRouter、Ollama…），A、B 可以是不同厂商：

```bash
A_BASE_URL=https://api.deepseek.com/v1  A_API_KEY=sk-...  A_MODEL=deepseek-chat \
B_BASE_URL=https://api.moonshot.cn/v1   B_API_KEY=sk-...  B_MODEL=moonshot-v1-8k \
NOTIFY_WEBHOOK_URL=https://api.day.app/你的BarkKey \
python3 orchestrator.py --task "……" --max-rounds 40 --cooldown 3
```

## v2 新增：接"网页桥"（Arena Agent 网页 / Tabbit 网页）

A、B 也可以是本地的网页桥（见 `../tabbit-arena-bridge/README-v2.md`）。网页自己记着上下文，所以要开"只发最新一条"模式：

| 变量（X = A 或 B） | 含义 |
| --- | --- |
| `X_SEND_ONLY_LAST=1` | 每次只发最新一条消息（第一次会把协作规则 + 任务合进去），并在请求体带 `timeout` 字段 |
| `X_TIMEOUT=3600` | 单次 HTTP 等待秒数（Agent 一轮可能很久） |
| `X_RETRIES=1` | 不重试（对网页桥来说重试 = 把同一句话再打进页面一次） |
| `X_WRAP="【… 第 {n} 轮发言】\n{text}"` | 转发给 X 时套的模板 |
| `X_ROLE="…"` | X 的分工说明，写进它的协作规则里。默认 A=有沙箱负责动手、B=没有执行环境负责审阅/给具体指令（防止对话模型假装执行）；设为空字符串则不加 |

其它：`--first-speaker A|B`、`--opener-file 文件`（很长的开场白）。停机标记只在**行首或最后一行**出现才算数，避免模型讨论中复述规则时误触发。

## 停机（= 需要人工干预）的判定

| 条件 | 触发方式 |
| --- | --- |
| 显式标记 | 任一方输出 `[DONE]` 或 `[NEED_HUMAN]`（可用 `STOP_TOKENS` 环境变量改） |
| 原地打转 | 同一方连续两轮内容 3-gram Jaccard 相似度 ≥ `--repeat-threshold`（默认 0.85） |
| 轮数上限 | `--max-rounds`（A+B 合计发言次数） |
| 接口异常 | 429/5xx 退避重试 `X_RETRIES` 次（默认 4）仍失败；4xx 直接停 |
| 人工组件 | 网页桥返回 `x_bridge.human_widget=true`（页面弹出需要人选的组件） |

触发后：终端响铃、写 `NEEDS_HUMAN.flag`（内容是原因）、可选 POST 到 `NOTIFY_WEBHOOK_URL`。

## 人工插话

运行中把话写进 `human_inbox.txt`，下一轮会以 `[HUMAN] …` 同时注入 A 和 B 的上下文，然后文件被删除。
