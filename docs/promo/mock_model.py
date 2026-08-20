"""OpenAI-compatible stand-in for the promo film.

The agent, retrieval tools, SSE pipeline, channels and WebSocket fan-out in
the film are the real product; only this model is scripted. Scenarios key on
the user's words, stream at a human pace, and always route through a real
search_brain tool call so the film shows the true tool loop.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8199
CHUNK_DELAY = 0.045

GARDEN_ANSWER = """Three things on the garden list for this weekend:

- **Repot the basil** before it bolts
- **Net the fig tree** — the magpies found it
- Water the rosemary on Friday, as every week

The winter garlic is already ordered and arrives Tuesday. Full list in \
vaults/shared/garden.md."""

KYOTO_ANSWER = """Kyoto is **Oct 12 → 19** — the Gion ryokan is booked. Two open \
tasks: activate the rail pass at Kansai (the activation window closes Oct 30) \
and book the Saihō-ji moss garden, which needs an advance postcard. Details in \
vaults/shared/trips/kyoto.md."""

DEFAULT_ANSWER = "That isn't in the brain yet — add a note to the shared vault and I'll find it."


def scenario(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if "garden" in lowered or "weekend" in lowered:
        return "garden tasks this weekend", GARDEN_ANSWER
    if "kyoto" in lowered or "trip" in lowered:
        return "kyoto trip dates and tasks", KYOTO_ANSWER
    return "", DEFAULT_ANSWER


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def do_POST(self):  # noqa: N802
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/embeddings"):
            data = [
                {"index": i, "embedding": [0.1, 0.2, 0.3]}
                for i in range(len(payload["input"]))
            ]
            body = json.dumps({"data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        messages = payload["messages"]
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        query, answer = scenario(str(last_user))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def frame(obj) -> None:
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
            self.wfile.flush()

        if query and not has_tool_result:
            frame(
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_brain",
                        "arguments": json.dumps({"query": query}),
                    },
                }]}}]}
            )
            time.sleep(0.4)
            frame({"choices": [{"finish_reason": "tool_calls", "delta": {}}]})
        else:
            words = answer.split(" ")
            for i, word in enumerate(words):
                piece = word if i == len(words) - 1 else word + " "
                frame({"choices": [{"delta": {"content": piece}}]})
                time.sleep(CHUNK_DELAY)
            frame(
                {"choices": [{"finish_reason": "stop", "delta": {}}],
                 "usage": {"prompt_tokens": 412, "completion_tokens": len(words)}}
            )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    print(f"mock model on :{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
