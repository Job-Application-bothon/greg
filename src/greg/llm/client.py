"""OpenAI-compatible LLM HTTP client wrapper."""
from __future__ import annotations

import json
import urllib.request

from greg.config import load_settings
from greg.llm.prompts import SYSTEM_PROMPT


def llm_headers() -> dict[str, str]:
    """Build HTTP headers for LLM API requests."""
    headers = {"Content-Type": "application/json"}
    api_key = load_settings().llm_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def resolve_model(base_url: str, configured_model: str) -> str:
    """Return configured model name or auto-discover from `{base_url}/models`."""
    if configured_model:
        return configured_model
    req = urllib.request.Request(f"{base_url}/models", headers=llm_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = data.get("data") or []
    if not models or not models[0].get("id"):
        raise ValueError("No models returned from LLM /models endpoint")
    return str(models[0]["id"])


def call_llm(messages: list[dict[str, str]]) -> dict[str, str]:
    """Forward chat messages to the configured OpenAI-compatible LLM endpoint (or OpenShift /api/chat relay)."""
    settings = load_settings()
    base_url = settings.llm_base_url
    if not base_url:
        raise RuntimeError("LLM_BASE_URL is not configured")

    full_messages = list(messages)
    if not full_messages or full_messages[0].get("role") != "system":
        full_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    if base_url.endswith("/api/chat"):
        payload = json.dumps({"messages": full_messages}).encode("utf-8")
        req = urllib.request.Request(
            base_url,
            data=payload,
            headers=llm_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if "reply" not in body:
            raise ValueError("Missing reply from OpenShift /api/chat relay")
        return {
            "reply": str(body["reply"]),
            "model": str(body.get("model") or settings.llm_model or "llama-32-3b-instruct"),
        }

    model = resolve_model(base_url, settings.llm_model)
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
        headers=llm_headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    choices = body.get("choices") or []
    if not choices:
        raise ValueError("Empty choices returned from LLM")
    reply = choices[0].get("message", {}).get("content", "")
    return {"reply": reply, "model": body.get("model", model)}
