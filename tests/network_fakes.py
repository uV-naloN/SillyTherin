"""A local server that stands in for OpenRouter so tests exercise real HTTP requests."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]


@dataclass
class FakeOpenRouter:
    base_url: str
    requests: list[RecordedRequest] = field(default_factory=list)


def stream_chunk(content: str, finish_reason: str | None) -> dict:
    delta = {"role": "assistant", "content": content}
    return {
        "id": "chunk-1", "object": "chat.completion.chunk", "created": 1, "model": "a/b",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def answer_events(answer: str) -> bytes:
    chunks = [stream_chunk(answer, None), stream_chunk("", "stop")]
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode() + b"data: [DONE]\n\n"


@contextmanager
def fake_openrouter(models: list[dict] | None = None, answer: str = "pong") -> Iterator[FakeOpenRouter]:
    """A local server that lists `models`, streams `answer`, and remembers every request it gets."""
    openrouter = FakeOpenRouter(base_url="")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.remember_request()
            self.reply(200, "application/json", json.dumps({"data": models or []}).encode())

        def do_POST(self) -> None:
            self.remember_request()
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.reply(200, "text/event-stream", answer_events(answer))

        def remember_request(self) -> None:
            headers = {name.lower(): value for name, value in self.headers.items()}
            openrouter.requests.append(RecordedRequest(self.command, self.path, headers))

        def reply(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_arguments: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    openrouter.base_url = f"http://127.0.0.1:{server.server_port}/api/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield openrouter
    finally:
        server.shutdown()
        server.server_close()

