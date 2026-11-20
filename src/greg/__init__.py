import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _health_payload() -> dict[str, object]:
    """Build the health and environment status payload."""
    llm_base_url = os.environ.get("LLM_BASE_URL", "")
    llm_model = os.environ.get("LLM_MODEL", "")
    return {
        "status": "ok",
        "service": "greg",
        "pod": socket.gethostname(),
        "llm_configured": bool(llm_base_url),
        "llm_model": llm_model or None,
    }


class Handler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for OpenShift smoke testing."""

    def do_GET(self) -> None:
        if self.path == "/health":
            body = json.dumps(_health_payload()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path in ("/", "/index.html"):
            html = (STATIC_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        self.send_error(404, "Not Found")

    def log_message(self, format: str, *args: object) -> None:
        # Flush stdout/stderr immediately so `oc logs` shows requests in real time.
        sys.stdout.write(f"{self.address_string()} - {format % args}\n")
        sys.stdout.flush()


def main() -> None:
    if "--check" in sys.argv:
        print(json.dumps(_health_payload()))
        return

    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"greg listening on 0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
