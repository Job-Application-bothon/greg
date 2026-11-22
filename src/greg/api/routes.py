"""HTTP request handler serving the REST API, health check, and hardened static frontend."""
from __future__ import annotations

import socket
import sys
import urllib.error
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from greg.api.serializers import encode_json, parse_int_or_default, parse_optional_int, read_json_body
from greg.config import STATIC_DIR, load_settings
from greg.domain.bank_mock import format_bank_account_label, pick_random_mock_bank_file
from greg.domain.ledger import LedgerStore
from greg.domain.sessions import SESSIONS
from greg.llm.classifier import classify_signatures_with_llm
from greg.llm.client import call_llm
from greg.llm.guardrails import GuardrailUnavailable, check_text, guardrails_enabled
from greg.llm.lookup import build_date_lookup
from greg.llm.prompts import (
    APP_GUIDE,
    NO_DATA_NOTE,
    SYSTEM_PROMPT,
    build_context_system_prompt,
    build_explain_prompt,
    is_refusal,
)
from greg.llm.topic import is_on_topic

CHAT_MAX_MESSAGES = 12
CHAT_MAX_CHARS = 4000
CHAT_ALLOWED_ROLES = {"user", "assistant"}
GUARDRAIL_REFUSAL = (
    "Sorry, I can't help with that. I can only answer questions about your business's money."
)


def _drop_refused_turns(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove refusals and the questions that caused them.

    Small models copy their own earlier replies, so a refusal left in history makes them
    refuse later on-topic questions too.
    """
    kept: list[dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "assistant" and is_refusal(msg["content"]):
            if kept and kept[-1]["role"] == "user":
                kept.pop()
            continue
        kept.append(msg)
    return kept


def sanitize_chat_messages(messages: list[Any]) -> list[dict[str, str]]:
    """Keep only well-formed user/assistant turns from the client, newest last.

    Client-supplied `system` messages are rejected so callers cannot replace Greg's
    grounded system prompt, and history is trimmed to the most recent turns.
    """
    cleaned: list[dict[str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            raise ValueError("Each message must be an object with 'role' and 'content'")
        role = msg.get("role")
        content = msg.get("content")
        if role not in CHAT_ALLOWED_ROLES:
            raise ValueError("Message role must be 'user' or 'assistant'")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Message content must be a non-empty string")
        if len(content) > CHAT_MAX_CHARS:
            raise ValueError(f"Messages are limited to {CHAT_MAX_CHARS} characters")
        cleaned.append({"role": role, "content": content})
    cleaned = _drop_refused_turns(cleaned)[-CHAT_MAX_MESSAGES:]
    if not cleaned or cleaned[-1]["role"] != "user":
        raise ValueError("The last message must come from the user")
    return cleaned

STATIC_MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}

_NO_STORE_SUFFIXES = {".html", ".css", ".js", ".mjs", ".json"}


def health_payload() -> dict[str, object]:
    """Build the runtime health and LLM readiness status payload."""
    settings = load_settings()
    return {
        "status": "ok",
        "service": "greg",
        "pod": socket.gethostname(),
        "llm_configured": bool(settings.llm_base_url),
        "llm_model": settings.llm_model or None,
        "guardrails_enabled": guardrails_enabled(),
    }


class GregRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the modular static UI, ledger/liquidity endpoints, and LLM proxy."""

    def _get_store(self) -> LedgerStore:
        sid = self.headers.get("X-Greg-Session", "").strip()
        return SESSIONS.get(sid or None)

    def _send_json(self, status: int, payload: Any) -> None:
        body = encode_json(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        cache_control: str | None = None,
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static_file(self, candidate: Path) -> None:
        suffix = candidate.suffix.lower()
        content_type = STATIC_MIME_TYPES.get(suffix, "application/octet-stream")
        if suffix in _NO_STORE_SUFFIXES:
            cache_control = "no-store"
        elif suffix == ".woff2":
            cache_control = "public, max-age=86400"
        else:
            cache_control = None
        self._send_bytes(candidate.read_bytes(), content_type, cache_control=cache_control)

    def do_GET(self) -> None:
        """Handle GET endpoints for health checks, ledger state, and static assets."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json(200, health_payload())
            return

        if path == "/api/view":
            qs = parse_qs(parsed.query)
            month = qs.get("month", [None])[0]
            horizon = parse_int_or_default(qs.get("horizon", ["30"])[0], 30)
            horizon_months = parse_optional_int(qs.get("horizon_months", [None])[0])
            store = self._get_store()
            self._send_json(
                200,
                store.get_view(
                    selected_month=month,
                    horizon_days=horizon,
                    horizon_months=horizon_months,
                ),
            )
            return

        if path in ("/assets/InterVariable.woff2", "/InterVariable.woff2"):
            font_path = STATIC_DIR / "fonts" / "InterVariable.woff2"
            if font_path.is_file():
                self._serve_static_file(font_path)
                return

        if path in ("/", "/index.html"):
            index_path = STATIC_DIR / "index.html"
            if index_path.is_file():
                self._serve_static_file(index_path)
                return

        if path.startswith("/static/"):
            static_root = STATIC_DIR.resolve()
            rel = unquote(path.removeprefix("/static/"))
            if not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
                self.send_error(404, "Not Found")
                return
            candidate = (static_root / rel).resolve()
            if not candidate.is_relative_to(static_root) or not candidate.is_file():
                self.send_error(404, "Not Found")
                return
            self._serve_static_file(candidate)
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        """Handle POST endpoints for CSV imports, manual operations, forecasts, and LLM queries."""
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            data = read_json_body(self.rfile, self.headers.get("Content-Length", "0"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid JSON request: {exc}"})
            return

        store = self._get_store()

        if path == "/api/import":
            self._handle_import(store, data)
            return

        if path == "/api/bank/connect":
            self._handle_bank_connect(store, data)
            return

        if path == "/api/operations":
            self._handle_add_operation(store, data)
            return

        if path == "/api/operations/delete":
            self._handle_delete_operation(store, data)
            return

        if path == "/api/opening-balance":
            self._handle_opening_balance(store, data)
            return

        if path == "/api/approve-credit":
            self._handle_approve_credit(store, data)
            return

        if path == "/api/reset":
            store.reset()
            self._send_json(200, store.get_view())
            return

        if path == "/api/explain":
            self._handle_explain(store, data)
            return

        if path == "/api/chat":
            self._handle_chat(store, data)
            return

        self.send_error(404, "Not Found")

    def _handle_import(self, store: LedgerStore, data: dict[str, Any]) -> None:
        csv_text = data.get("csv_text", "")
        filename = str(data.get("filename") or "statement.csv")
        column_map = data.get("column_map")
        if column_map is not None and not isinstance(column_map, dict):
            self._send_json(400, {"error": "'column_map' must be a JSON object"})
            return
        try:
            opening_balance = float(data.get("opening_balance", 0.0))
            horizon = parse_int_or_default(data.get("horizon_days"), 30)
            horizon_months = parse_optional_int(data.get("horizon_months"))
            keep_manual = bool(data.get("keep_manual", False))
            store.import_csv(
                csv_text=csv_text,
                filename=filename,
                opening_balance=opening_balance,
                keep_manual=keep_manual,
                llm_classifier=classify_signatures_with_llm,
                column_map=column_map,
            )
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, store.get_view(horizon_days=horizon, horizon_months=horizon_months))

    def _handle_bank_connect(self, store: LedgerStore, data: dict[str, Any]) -> None:
        settings = load_settings()
        try:
            chosen = pick_random_mock_bank_file(
                settings.bank_mock_dir,
                exclude_filename=store.filename,
            )
        except FileNotFoundError as exc:
            self._send_json(404, {"error": str(exc)})
            return

        account_name = format_bank_account_label(chosen.name)
        try:
            csv_text = chosen.read_text(encoding="utf-8-sig")
            opening_balance = float(data.get("opening_balance", 0.0))
            horizon = parse_int_or_default(data.get("horizon_days"), 30)
            horizon_months = parse_optional_int(data.get("horizon_months"))
            store.import_csv(
                csv_text=csv_text,
                filename=chosen.name,
                opening_balance=opening_balance,
                keep_manual=False,
                llm_classifier=classify_signatures_with_llm,
                data_source="connected_bank",
                bank_account_name=account_name,
            )
        except (OSError, ValueError) as exc:
            self._send_json(400, {"error": f"Failed to load connected bank statement: {exc}"})
            return
        self._send_json(200, store.get_view(horizon_days=horizon, horizon_months=horizon_months))

    def _handle_add_operation(self, store: LedgerStore, data: dict[str, Any]) -> None:
        try:
            op = store.add_manual_operation(
                date_str=str(data.get("date") or ""),
                kind=str(data.get("kind") or "expense"),
                amount=float(data.get("amount", 0)),
                counterparty=str(data.get("counterparty") or ""),
                category=str(data.get("category") or "Other"),
                description=str(data.get("description") or ""),
            )
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        month = data.get("selected_month") or op["date"][:7]
        horizon = parse_int_or_default(data.get("horizon_days"), 30)
        horizon_months = parse_optional_int(data.get("horizon_months"))
        self._send_json(
            200,
            store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months),
        )

    def _handle_delete_operation(self, store: LedgerStore, data: dict[str, Any]) -> None:
        store.delete_operation(str(data.get("id") or ""))
        month = data.get("selected_month")
        horizon = parse_int_or_default(data.get("horizon_days"), 30)
        horizon_months = parse_optional_int(data.get("horizon_months"))
        self._send_json(
            200,
            store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months),
        )

    def _handle_opening_balance(self, store: LedgerStore, data: dict[str, Any]) -> None:
        try:
            store.set_opening_balance(float(data.get("opening_balance", 0.0)))
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        month = data.get("selected_month")
        horizon = parse_int_or_default(data.get("horizon_days"), 30)
        horizon_months = parse_optional_int(data.get("horizon_months"))
        self._send_json(
            200,
            store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months),
        )

    def _handle_approve_credit(self, store: LedgerStore, data: dict[str, Any]) -> None:
        try:
            amount = float(data.get("amount", 0.0))
            month = str(data.get("selected_month") or "")
            horizon = parse_int_or_default(data.get("horizon_days"), 30)
            horizon_months = parse_optional_int(data.get("horizon_months"))
            store.approve_credit(amount=amount, month=month, horizon_days=horizon)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(
            200,
            store.get_view(
                selected_month=month or None,
                horizon_days=horizon,
                horizon_months=horizon_months,
            ),
        )

    def _handle_explain(self, store: LedgerStore, data: dict[str, Any]) -> None:
        month = data.get("selected_month")
        horizon = parse_int_or_default(data.get("horizon_days"), 30)
        horizon_months = parse_optional_int(data.get("horizon_months"))
        view = store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months)
        if not view.get("has_data"):
            self._send_json(400, {"error": "Import a CSV or add an operation first."})
            return
        la = view["liquidity_agent"]
        prompt = build_explain_prompt(view)
        sys_prompt = build_context_system_prompt(
            selected_month=view["selected_month"],
            horizon_days=horizon,
            horizon_months=horizon_months,
            store=store,
        )
        try:
            res = call_llm(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ]
            )
            self._send_json(200, {"reply": res["reply"], "model": res["model"], "source": "llm"})
        except Exception as exc:
            fallback = (
                f"{la['analysis_summary']}\n\n"
                f"{la['recommendation_summary']}\n\n"
                f"(Note: OpenShift AI LLM was unreachable: {exc})"
            )
            self._send_json(
                200, {"reply": fallback, "model": "built-in-summary", "source": "fallback"}
            )

    def _handle_chat(self, store: LedgerStore, data: dict[str, Any]) -> None:
        messages = data.get("messages")
        if not messages and isinstance(data.get("message"), str):
            messages = [{"role": "user", "content": data["message"]}]

        if not isinstance(messages, list) or not messages:
            self._send_json(
                400, {"error": "Provide a non-empty 'messages' list or 'message' string"}
            )
            return

        try:
            history = sanitize_chat_messages(messages)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            for msg in history:
                verdict = check_text(msg["content"])
                if not verdict.allowed:
                    self._send_json(
                        422,
                        {"error": GUARDRAIL_REFUSAL, "blocked": True, "rails": list(verdict.rails)},
                    )
                    return
        except GuardrailUnavailable as exc:
            self._send_json(503, {"error": str(exc)})
            return

        raw_period = data.get("selected_period")
        selected_period = raw_period if isinstance(raw_period, dict) else None
        if not is_on_topic(history, selected_period):
            self._send_json(422, {"error": GUARDRAIL_REFUSAL, "blocked": True, "rails": ["topic"]})
            return

        month = data.get("selected_month")
        horizon = parse_int_or_default(data.get("horizon_days"), 30)
        horizon_months = parse_optional_int(data.get("horizon_months"))
        system_prompt = (
            build_context_system_prompt(
                selected_month=month,
                horizon_days=horizon,
                horizon_months=horizon_months,
                store=store,
                selected_period=selected_period,
            )
            if store.transactions
            else f"{SYSTEM_PROMPT}\n\n{NO_DATA_NOTE}\n\n{APP_GUIDE}"
        )
        if store.transactions:
            system_prompt = f"{system_prompt}\n\n{APP_GUIDE}"
        recent_questions = [m["content"] for m in history if m["role"] == "user"][-2:]
        lookup = build_date_lookup(store, "\n".join(recent_questions))
        if lookup:
            system_prompt = f"{system_prompt}\n\n{lookup}"
        call_messages = [{"role": "system", "content": system_prompt}, *history]

        try:
            result = call_llm(call_messages)
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
        """Flush HTTP access logs immediately for OpenShift pod streaming."""
        sys.stdout.write(f"{self.address_string()} - {format % args}\n")
        sys.stdout.flush()
