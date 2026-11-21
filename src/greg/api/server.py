"""HTTP server construction and CLI entrypoint for Greg."""
from __future__ import annotations

import json
import sys
from http.server import ThreadingHTTPServer

from greg.api.routes import GregRequestHandler, health_payload
from greg.config import load_settings


def create_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    """Create a configured `ThreadingHTTPServer` bound to `(host, port)`."""
    settings = load_settings()
    bind_host = settings.host if host is None else host
    bind_port = settings.port if port is None else int(port)
    return ThreadingHTTPServer((bind_host, bind_port), GregRequestHandler)


def main(argv: list[str] | None = None) -> None:
    """Run health smoke check (`--check`) or serve the Greg web application."""
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        print(json.dumps(health_payload()))
        return

    server = create_server()
    bind_host, bind_port = server.server_address[:2]
    print(f"greg listening on {bind_host}:{bind_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
