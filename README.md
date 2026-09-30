# tabbit ⇄ arena-agent

让 **Tabbit 浏览器里的网页对话**（web.tabbit.ai，自己选模型）和 **arena.ai 的 Agent 模式网页** 自动来回对话，
直到任务完成（`[DONE]`）或真的需要人拍板（`[NEED_HUMAN]`）。全部在本机、两边都是网页，不用云 API、不花钱。

> 自动化 arena.ai 违反其服务条款，可能被封号；网页改版会让选择器失效。使用者自担风险。

## 从哪里开始

| 你是谁 | 读这个 |
| --- | --- |
| 接手的 AI / 协作者 | [`HANDOFF.md`](HANDOFF.md) —— 目标、硬约束、现状、唯一阻塞点、下一步 |
| 要跑起来 | [`tabbit-arena-bridge/README-v2.md`](tabbit-arena-bridge/README-v2.md) —— 安装、打补丁、装扩展、三窗口运行、FAQ |
| 想知道为什么这么设计 | [`Arena-Agent与Tabbit模型自动互通可行性分析.md`](Arena-Agent与Tabbit模型自动互通可行性分析.md) |

## 目录

```
tabbit-arena-bridge/   主体：bridge.py（CDP 库）、tabbit_web_api.py、survey_clicker.py、doctor.py、
                       orchestrator.py、aab/patch_aab.py（给 ArenaAgentBridge 打补丁）、start-v2.ps1、test/ 模拟页
ai-pingpong/           编排器的独立副本 + mock 服务器（与 tabbit-arena-bridge/orchestrator.py 保持一致）
HANDOFF.md             交接文档
push-to-github.ps1     一键提交并推送到本仓库
```

上游依赖（单独 clone，不放进本仓库）：[startify2647/ArenaAgentBridge](https://github.com/startify2647/ArenaAgentBridge)，
用 `python tabbit-arena-bridge/aab/patch_aab.py --repo <clone 路径>` 打补丁并重新打包。

## 状态（2026-09-29）

- Arena 侧：ArenaAgentBridge + 补丁已在 Tabbit 里连通，单句往返成功；**卡在**答完后的"此任务成功了吗?"问卷不能自动点掉 → `survey_clicker.py`（CDP 真实点击阶梯）+ `doctor.py` 待真机验证。
- Tabbit 侧：选择器已从 web.tabbit.ai 的前端代码里读出并预填（`data-message-type`、`#ChatSendButton`、Tiptap 输入框），`tabbit_web_api.py` 对同结构模拟页通过；真实页面待冒烟。
- 编排器：mock + 模拟页端到端通过。
