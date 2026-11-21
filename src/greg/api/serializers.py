"""Request body parsing and JSON serialization helpers for the HTTP layer."""
from __future__ import annotations

import json
from typing import Any


def parse_optional_int(value: Any) -> int | None:
    """Parse an optional integer parameter, returning `None` for empty or invalid values."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_json_body(rfile: Any, content_length_header: str) -> dict[str, Any]:
    """Read and decode a JSON object request body."""
    length = int(content_length_header or "0")
    raw = rfile.read(length) if length > 0 else b"{}"
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def encode_json(payload: Any) -> bytes:
    """Encode a Python object as UTF-8 JSON bytes."""
    return json.dumps(payload).encode("utf-8")
