#!/usr/bin/env python3
"""
mock_openai_server.py —— 一个假的 OpenAI 兼容服务器，用来在没有 API key 的情况下
验证 orchestrator.py 的循环 / 停机 / 人工插话逻辑。

行为：
  * 模型名含 "a" 的一方每轮输出"方案第 N 步…"；
  * 模型名含 "b" 的一方前几轮提修改意见，第 5 次被调用时输出 [DONE]；
  * 只依赖标准库。监听 127.0.0.1:8123
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

calls = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安静
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        model = body.get("model", "mock")
        calls[model] = calls.get(model, 0) + 1
        k = calls[model]
        last_user = next((m["content"] for m in reversed(body.get("messages", [])) if m["role"] == "user"), "")
        if "[HUMAN]" in last_user:
            text = f"收到人类插话，已调整。（{model} 第 {k} 次）"
        elif "mock-b" in model:
            text = "[DONE] 我确认代码已通过 review，任务完成。" if k >= 5 else f"第 {k} 次 review：请把边界条件补上，然后加两个单元测试。"
        else:
            text = f"方案第 {k} 步：这是我写的排序函数 v{k}，请 review。\n```python\ndef sort_v{k}(xs):\n    return sorted(xs)\n```"
        resp = {"id": "mock", "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
        out = json.dumps(resp, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    print("mock OpenAI server on http://127.0.0.1:8123/v1")
    HTTPServer(("127.0.0.1", 8123), H).serve_forever()
