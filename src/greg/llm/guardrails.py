"""Optional NeMo Guardrails input screening via the OpenShift AI `/v1/guardrail/checks` API."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from greg.config import load_settings

SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
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
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network, HTTP, or JSON errors
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
