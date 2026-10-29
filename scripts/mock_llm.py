#!/usr/bin/env python3
"""A tiny fake AI provider, so you can try the whole system without an API key.

    python scripts/mock_llm.py            # listens on http://127.0.0.1:9099/v1

Then in backend/.env:

    LLM_BASE_URL=http://127.0.0.1:9099/v1
    LLM_MODEL=mock

It speaks the same /v1/chat/completions protocol as OpenAI, Gemini, Ollama and
the rest, but returns canned, clearly-labelled text. Use it to:
  * confirm your install works end to end before spending anything;
  * run the test suite and `scripts/doctor.py --e2e` offline;
  * demo the dashboard.

The notes it produces are NOT real summaries. Switch to a real provider
(docs/CONFIGURATION.md) before relying on the output.

Standard library only. Listens on loopback only.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def answer(prompt: str) -> str:
    p = prompt or ""
    if p.startswith("Classify this meeting"):
        return "team"
    if "## Meeting Title" in p:                        # the summary prompt asks for this heading
        return ("## Meeting Title\nDemo Meeting (mock AI)\n\n## Metadata\n- **Type:** team\n\n"
                "## Summary\nThis is a canned summary from scripts/mock_llm.py, not a real AI. "
                "Point LLM_BASE_URL at a real provider to get real notes.\n\n"
                "## Decisions\n- The pipeline is wired up correctly.\n")
    return ("## Action Plan\n\n### Urgent & Important\n"
            "- [ ] Switch LLM_BASE_URL from the mock to a real provider (Owner: you, Due: today)\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                          # keep the console quiet
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        prompt = (body.get("messages") or [{}])[-1].get("content", "")
        self._send(200, {
            "id": "mock-1", "object": "chat.completion", "model": body.get("model", "mock"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": answer(prompt)}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9099)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock AI provider on http://127.0.0.1:{args.port}/v1  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
