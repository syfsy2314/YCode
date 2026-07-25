"""用于官方 SDK 集成测试的本机 SSE 服务。"""

import json
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass(slots=True)
class RecordedRequest:
    path: str
    headers: dict[str, str]
    json: dict[str, Any]


@dataclass(slots=True)
class StreamResponse:
    events: list[str] = field(default_factory=list)
    status: int = 200
    delay: float = 0.0
    error_body: dict[str, Any] | None = None
    disconnect_after: int | None = None


def sse_event(event: str | None, data: dict[str, Any] | str) -> str:
    lines: list[str] = []
    if event is not None:
        lines.append(f"event: {event}")
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


class SSETestServer:
    def __init__(self) -> None:
        self.responses: deque[StreamResponse] = deque()
        self.requests: list[RecordedRequest] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def origin(self) -> str:
        if self._server is None:
            raise RuntimeError("SSETestServer 尚未启动")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def base_url(self) -> str:
        return f"{self.origin}/v1"

    def enqueue(self, response: StreamResponse) -> None:
        self.responses.append(response)

    def start(self) -> None:
        if self._server is not None:
            return

        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    payload = {}
                with owner._lock:
                    owner.requests.append(
                        RecordedRequest(
                            path=self.path,
                            headers={key.lower(): value for key, value in self.headers.items()},
                            json=payload,
                        )
                    )
                    response = owner.responses.popleft() if owner.responses else StreamResponse()

                if response.status != 200:
                    body = json.dumps(response.error_body or {"error": "test error"}).encode()
                    self.send_response(response.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                    self.close_connection = True
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for index, event in enumerate(response.events, start=1):
                    try:
                        self.wfile.write(event.encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    if response.disconnect_after == index:
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        self.connection.close()
                        return
                    if response.delay:
                        time.sleep(response.delay)
                self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def __enter__(self) -> "SSETestServer":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
