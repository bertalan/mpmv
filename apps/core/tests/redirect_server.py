"""A local HTTP server that answers every request with a redirect to another host (SEC.5).

The redirect target is the same server under the name `localhost`, while the request went to
`127.0.0.1`: a transport that followed it would hit the server a second time, and `paths` shows it.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class RedirectServer:
    base_url: str
    paths: list[str] = field(default_factory=list)


@contextmanager
def redirect_server(status: int = 302) -> Iterator[RedirectServer]:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def _redirect(self) -> None:
            hits.append(self.path)
            port = str(self.headers.get("Host", "")).rpartition(":")[2]
            self.send_response(status)
            self.send_header("Location", f"http://localhost:{port}/followed?leak=1")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _redirect
        do_POST = _redirect

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield RedirectServer(base_url=f"http://127.0.0.1:{server.server_address[1]}", paths=hits)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
