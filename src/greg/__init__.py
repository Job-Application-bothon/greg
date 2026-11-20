import json
import os
import socket
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

STATIC_DIR = Path(__file__).resolve().parent / "static"
SYSTEM_PROMPT = (
    "You are Greg, an AI-powered Autonomous CFO assistant for SMEs. "
    "Provide clear, explainable financial guidance and highlight next steps."
)


def _health_payload() -> dict[str, object]:
    """Build the health and environment status payload."""
    llm_base_url = os.environ.get("LLM_BASE_URL", "").strip()
    llm_model = os.environ.get("LLM_MODEL", "").strip()
    return {
        "status": "ok",
        "service": "greg",
        "pod": socket.gethostname(),
        "llm_configured": bool(llm_base_url),
        "llm_model": llm_model or None,
    }


def _llm_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _resolve_model(base_url: str, configured_model: str) -> str:
    if configured_model:
        return configured_model
    req = urllib.request.Request(f"{base_url}/models", headers=_llm_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = data.get("data") or []
    if not models or not models[0].get("id"):
        raise ValueError("No models returned from LLM /models endpoint")
    return str(models[0]["id"])


def call_llm(messages: list[dict[str, str]]) -> dict[str, str]:
    """Forward chat messages to the configured OpenAI-compatible LLM endpoint."""
    base_url = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("LLM_BASE_URL is not configured")

    model = _resolve_model(base_url, os.environ.get("LLM_MODEL", "").strip())
    full_messages = list(messages)
    if not full_messages or full_messages[0].get("role") != "system":
        full_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    payload = json.dumps(
        {
            "model": model,
            "messages": full_messages,
            "max_tokens": 512,
            "temperature": 0.3,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers=_llm_headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    choices = body.get("choices") or []
    if not choices:
        raise ValueError("Empty choices returned from LLM")
    reply = choices[0].get("message", {}).get("content", "")
    return {"reply": reply, "model": body.get("model", model)}


class Handler(BaseHTTPRequestHandler):
    """HTTP handler serving the chat frontend, health check, and LLM proxy."""

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, _health_payload())
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

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404, "Not Found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid JSON request: {exc}"})
            return

        messages = data.get("messages")
        if not messages and isinstance(data.get("message"), str):
            messages = [{"role": "user", "content": data["message"]}]

        if not isinstance(messages, list) or not messages:
            self._send_json(400, {"error": "Provide a non-empty 'messages' list or 'message' string"})
            return

        try:
            result = call_llm(messages)
        except RuntimeError as exc:
            self._send_json(503, {"error": str(exc)})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._send_json(502, {"error": f"LLM HTTP {exc.code}: {detail}"})
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            self._send_json(502, {"error": f"LLM request failed: {exc}"})
        else:
            self._send_json(200, result)

    def log_message(self, format: str, *args: object) -> None:
        # Flush stdout immediately so `oc logs` shows requests in real time.
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
