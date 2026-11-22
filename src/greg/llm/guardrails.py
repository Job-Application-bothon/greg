"""Optional NeMo Guardrails input screening via the OpenShift AI `/v1/guardrail/checks` API."""
from __future__ import annotations

import hashlib
import json
import ssl
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from greg.config import load_settings

SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
# OpenShift mounts the cluster's service CA here; it signs in-cluster `https://*.svc` certificates.
SERVICE_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt")
_CACHE_MAX = 512


class GuardrailUnavailable(RuntimeError):
    """Raised when guardrails are configured but the check service cannot be reached."""


@dataclass(frozen=True)
class GuardrailVerdict:
    allowed: bool
    rails: tuple[str, ...] = ()


_cache: OrderedDict[str, GuardrailVerdict] = OrderedDict()
_cache_lock = threading.Lock()


def guardrails_enabled() -> bool:
    return bool(load_settings().guardrails_check_url)


def _auth_token() -> str:
    token = load_settings().guardrails_token
    if token:
        return token
    try:
        return SA_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _ssl_context() -> ssl.SSLContext:
    """System CAs plus the OpenShift service CA (or `GUARDRAILS_CA_FILE`) when present."""
    ctx = ssl.create_default_context()
    ca_file = load_settings().guardrails_ca_file
    ca_path = Path(ca_file) if ca_file else SERVICE_CA_PATH
    if ca_path.is_file():
        ctx.load_verify_locations(cafile=str(ca_path))
    return ctx


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        detail = exc.read().decode("utf-8", errors="replace").strip()
    except OSError:
        detail = ""
    hint = ""
    if exc.code == 400 and exc.url.startswith("http://"):
        hint = " (if this port serves HTTPS, use an https:// GUARDRAILS_CHECK_URL)"
    return f"HTTP {exc.code}{': ' + detail[:300] if detail else ''}{hint}"


def _parse_verdict(body: dict) -> GuardrailVerdict:
    """Interpret a checks response: blocked if the top-level or any rail status is `blocked`."""
    blocked = str(body.get("status", "")).lower() == "blocked"
    rails_status = body.get("rails_status") or {}
    fired = []
    if isinstance(rails_status, dict):
        for name, info in rails_status.items():
            if isinstance(info, dict) and str(info.get("status", "")).lower() == "blocked":
                blocked = True
                fired.append(str(name))
    return GuardrailVerdict(allowed=not blocked, rails=tuple(fired))


def _check_remote(text: str) -> GuardrailVerdict:
    settings = load_settings()
    headers = {"Content-Type": "application/json"}
    token = _auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(
        {"model": "greg", "messages": [{"role": "user", "content": text}]}
    ).encode("utf-8")
    req = urllib.request.Request(
        settings.guardrails_check_url, data=payload, headers=headers, method="POST"
    )
    context = _ssl_context() if req.full_url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=settings.guardrails_timeout, context=context) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GuardrailUnavailable(f"Guardrails check failed: {_http_error_detail(exc)}") from exc
    except Exception as exc:  # network, TLS, or JSON errors
        raise GuardrailUnavailable(f"Guardrails check failed: {exc}") from exc
    if not isinstance(body, dict):
        raise GuardrailUnavailable("Guardrails check returned an unexpected payload")
    return _parse_verdict(body)


def check_text(text: str) -> GuardrailVerdict:
    """Screen one piece of untrusted text. Always allowed when guardrails are not configured."""
    if not guardrails_enabled() or not text.strip():
        return GuardrailVerdict(allowed=True)
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    try:
        verdict = _check_remote(text)
    except GuardrailUnavailable:
        if load_settings().guardrails_fail_open:
            return GuardrailVerdict(allowed=True)
        raise
    with _cache_lock:
        _cache[key] = verdict
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return verdict


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
