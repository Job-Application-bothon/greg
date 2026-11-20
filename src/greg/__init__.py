from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from greg import engine

STATIC_DIR = Path(__file__).resolve().parent / "static"
SYSTEM_PROMPT = (
    "You are Greg, an AI-powered Autonomous CFO assistant for SMEs. "
    "Provide clear, explainable financial guidance and highlight next steps."
)

STATE_LOCK = threading.RLock()


def _default_state() -> dict[str, Any]:
    return {
        "scenario_id": "pizzeria_amalfi",
        "custom_csv_text": None,
        "custom_filename": None,
        "opening_balance": 2500.0,
        "profile_overrides": {},
        "preferences": {},
        "action_overrides": {},
        "sim_params": {
            "revenue_change_pct": 0.0,
            "delay_days": 0,
            "fx_shock_pct": 0.0,
            "extra_monthly_cost_eur": 0.0,
            "include_commerzbank_actions": True,
        },
        "receipts": [],
        "audit_ledger": [],
        "ai_briefing": None,
        "last_ai_trace": None,
    }


def _state_file_path() -> Path:
    custom = os.environ.get("GREG_STATE_FILE", "").strip()
    if custom:
        return Path(custom)
    return Path(tempfile.gettempdir()) / "greg_cfo_state.json"


def _load_initial_state() -> dict[str, Any]:
    base = _default_state()
    path = _state_file_path()
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("scenario_id"):
                base.update(loaded)
                return base
    except (OSError, ValueError):
        pass
    engine.create_audit_entry(
        base["audit_ledger"],
        actor="SYSTEM",
        action_type="INIT_WORKSPACE",
        target_id="pizzeria_amalfi",
        summary="Initialized Autonomous CFO workspace with Pizzeria Amalfi 2025 CSV (1,125 bookings).",
    )
    return base


STATE: dict[str, Any] = _load_initial_state()


def _save_state() -> None:
    path = _state_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(STATE), encoding="utf-8")
    except OSError:
        # In read-only environments, continue cleanly in memory.
        pass


def reset_state(scenario_id: str = "pizzeria_amalfi") -> dict[str, Any]:
    """Reset workspace state (primarily for tests and scenario switches)."""
    with STATE_LOCK:
        fresh = _default_state()
        fresh["scenario_id"] = scenario_id
        engine.create_audit_entry(
            fresh["audit_ledger"],
            actor="USER",
            action_type="SCENARIO_LOAD",
            target_id=scenario_id,
            summary=f"Loaded scenario '{scenario_id}'.",
        )
        STATE.clear()
        STATE.update(fresh)
        _save_state()
        return get_full_state()


def get_current_bundle() -> dict[str, Any]:
    with STATE_LOCK:
        return engine.build_scenario_bundle(
            scenario_id=STATE["scenario_id"],
            custom_csv_text=STATE.get("custom_csv_text"),
            custom_filename=STATE.get("custom_filename"),
            opening_balance=float(STATE.get("opening_balance", 2500.0)),
            profile_overrides=STATE.get("profile_overrides"),
            preferences=STATE.get("preferences"),
            action_overrides=STATE.get("action_overrides"),
            sim_params=STATE.get("sim_params"),
        )


def get_full_state() -> dict[str, Any]:
    with STATE_LOCK:
        bundle = get_current_bundle()
        if not STATE.get("last_ai_trace"):
            anon_sys, token_map = engine.build_anonymized_context_prompt(bundle)
            STATE["last_ai_trace"] = {
                "timestamp": None,
                "mode": "preview",
                "model": os.environ.get("LLM_MODEL", "").strip() or "llama-32-3b-instruct",
                "latency_ms": None,
                "anonymized_system_prompt": anon_sys,
                "anonymized_user_messages": [],
                "masked_tokens": [{"token": k, "category": _token_kind(k)} for k in token_map],
                "token_count": len(token_map),
            }
        briefing = STATE.get("ai_briefing") or {
            "text": engine.build_deterministic_briefing(bundle),
            "mode": "deterministic_baseline",
            "model": os.environ.get("LLM_MODEL", "").strip() or "llama-32-3b-instruct",
            "generated_at": None,
        }
        return {
            **bundle,
            "llm_status": _health_payload(),
            "ai_briefing": briefing,
            "last_ai_trace": STATE["last_ai_trace"],
            "receipts": list(reversed(STATE.get("receipts", []))),
            "audit_ledger": list(reversed(STATE.get("audit_ledger", []))),
        }


def _token_kind(token: str) -> str:
    if token.startswith("IBAN_TOK_"):
        return "Bank Account / IBAN"
    if token.startswith("OWNER_REF"):
        return "Owner PII"
    if token.startswith("PARTNER"):
        return "Partner PII"
    if token.startswith("STAFF"):
        return "Employee PII"
    if token.startswith("CLIENT"):
        return "Customer Entity"
    if token.startswith("VENDOR"):
        return "Supplier Entity"
    return "Business Entity"


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
        "llm_base_url": llm_base_url or None,
        "active_scenario": STATE.get("scenario_id", "pizzeria_amalfi"),
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


def call_llm(messages: list[dict[str, str]], max_tokens: int = 512) -> dict[str, str]:
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
            "max_tokens": max_tokens,
            "temperature": 0.25,
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


def _deterministic_cfo_chat_reply(bundle: dict[str, Any], prompt_text: str) -> str:
    q = (prompt_text or "").lower()
    k = bundle["kpis"]
    p = bundle["profile"]
    sim = bundle["simulation"]["summary"]
    actions = bundle["actions"]

    if any(w in q for w in ("fraud", "iban", "quarantine", "invoice")):
        q_inv = bundle["quarantined_invoice"]
        return (
            f"Input Guard quarantined {q_inv['invoice_id']} ({q_inv['vendor']}, €{q_inv['invoice_amount_eur']:,.2f}). "
            f"Reason: {q_inv['reason']} Payment release is hard-blocked until you explicitly confirm or override."
        )
    if any(w in q for w in ("chf", "usd", "fx", "hedge", "zurich", "forward")):
        fx_act = next((a for a in actions if a["category"] == "fx"), None)
        if fx_act:
            return (
                f"FX Exposure Analysis ({k['fx_exposure_label']}): {fx_act['autonomy']['analyzed_independently']} "
                f"Recommended Commerzbank Action: {fx_act['autonomy']['recommended_logic']}"
            )
    if any(w in q for w in ("tax", "vat", "steuer", "grant", "kfw", "law", "regulat")):
        regs = bundle["regulatory_updates"]
        top_reg = regs[0]["title"] if regs else "German SME tax monitoring"
        return (
            f"Tax & Regulatory Brief for {p['business_name']}: Matched {len(regs)} official German update(s), led by '{top_reg}'. "
            "Your recurring tax calendar and Commerzbank Tax Vault rule are aligned in the Regulatory & Tax tab."
        )
    if any(w in q for w in ("festgeld", "invest", "surplus", "deposit")):
        inv_act = next((a for a in actions if a["category"] == "investment"), None)
        if inv_act:
            return f"Surplus Opportunity: {inv_act['autonomy']['recommended_logic']}"

    return (
        f"CFO Brief for {p['business_name']}: Current cash is €{k['current_balance_eur']:,.2f} "
        f"(13-week baseline low: €{k['projected_min_13w_eur']:,.2f} in Week {k['projected_min_13w_week']}). "
        f"With the recommended Commerzbank actions, the 13-week minimum stabilizes at €{sim['min_stabilized_eur']:,.2f} "
        f"(vs. €{k['safety_buffer_eur']:,.0f} safety buffer). All figures are deterministically verified; human approval is required before execution."
    )


def invoke_cfo_llm(
    user_messages: list[dict[str, str]],
    include_context: bool = True,
    max_tokens: int = 512,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Send PII-anonymized Unified Business Context + user prompt to the single OpenShift AI LLM."""
    bundle = get_current_bundle()
    anon_sys, token_map = engine.build_anonymized_context_prompt(bundle)
    merged_tokens = dict(token_map)

    cleaned_msgs: list[dict[str, str]] = []
    for m in user_messages:
        role = m.get("role", "user")
        content = str(m.get("content", ""))
        if role == "system":
            continue
        masked_c, extra_toks = engine.anonymize_text(content, profile=bundle["profile"])
        merged_tokens.update(extra_toks)
        cleaned_msgs.append({"role": role, "content": masked_c})

    payload_msgs = ([{"role": "system", "content": anon_sys}] + cleaned_msgs) if include_context else list(user_messages)

    start = time.monotonic()
    mode = "openshift_ai_live"
    llm_error: str | None = None
    try:
        res = call_llm(payload_msgs, max_tokens=max_tokens)
        reply = engine.deanonymize_text(res["reply"], merged_tokens) if include_context else res["reply"]
        model_used = res["model"]
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        if not allow_fallback:
            raise
        mode = "deterministic_fallback"
        llm_error = str(exc)
        last_user = next((m["content"] for m in reversed(user_messages) if m.get("role") == "user"), "")
        reply = _deterministic_cfo_chat_reply(bundle, last_user)
        model_used = os.environ.get("LLM_MODEL", "").strip() or "llama-32-3b-instruct"

    latency_ms = round((time.monotonic() - start) * 1000, 1)
    with STATE_LOCK:
        STATE["last_ai_trace"] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": mode,
            "model": model_used,
            "latency_ms": latency_ms,
            "llm_error": llm_error,
            "anonymized_system_prompt": anon_sys,
            "anonymized_user_messages": cleaned_msgs,
            "masked_tokens": [{"token": k, "category": _token_kind(k)} for k in merged_tokens],
            "token_count": len(merged_tokens),
        }
        _save_state()

    return {
        "reply": reply,
        "model": model_used,
        "mode": mode,
        "latency_ms": latency_ms,
        "llm_error": llm_error,
        "masked_token_count": len(merged_tokens),
    }


class Handler(BaseHTTPRequestHandler):
    """HTTP handler serving the Autonomous CFO UI, financial engine API, health check, and OpenShift AI proxy."""

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                self._send_json(400, {"error": "JSON body must be an object"})
                return None
            return data
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid JSON request: {exc}"})
            return None

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json(200, _health_payload())
            return

        if path == "/api/state":
            self._send_json(200, get_full_state())
            return

        if path in ("/", "/index.html"):
            html = (STATIC_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        data = self._read_json()
        if data is None:
            return

        if path == "/api/scenario":
            scenario_id = str(data.get("scenario_id", "pizzeria_amalfi")).strip()
            if scenario_id not in ("pizzeria_amalfi", "mittelstand_b2b"):
                self._send_json(400, {"error": f"Unsupported scenario_id: {scenario_id}"})
                return
            self._send_json(200, reset_state(scenario_id))
            return

        if path == "/api/upload-csv":
            csv_text = str(data.get("csv_text", ""))
            filename = str(data.get("filename", "uploaded_statement.csv"))
            opening_bal = float(data.get("opening_balance", 2500.0))
            try:
                parsed_preview = engine.parse_bank_csv(csv_text)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            with STATE_LOCK:
                STATE["scenario_id"] = "custom_csv"
                STATE["custom_csv_text"] = csv_text
                STATE["custom_filename"] = filename
                STATE["opening_balance"] = opening_bal
                STATE["profile_overrides"] = {}
                STATE["action_overrides"] = {}
                STATE["receipts"] = []
                STATE["ai_briefing"] = None
                engine.create_audit_entry(
                    STATE["audit_ledger"],
                    actor="USER",
                    action_type="CSV_INGESTION",
                    target_id=filename,
                    summary=f"Ingested custom CSV '{filename}' ({parsed_preview['unique_count']} clean rows, hygiene {parsed_preview['hygiene']['score']}%).",
                )
                _save_state()
            self._send_json(200, get_full_state())
            return

        if path == "/api/simulate":
            query = str(data.get("query", "")).strip()
            with STATE_LOCK:
                if query:
                    parsed_q = engine.parse_what_if_query(query)
                    STATE["sim_params"] = parsed_q
                    engine.create_audit_entry(
                        STATE["audit_ledger"],
                        actor="USER",
                        action_type="NL_WHATIF_SIM",
                        target_id="simulator",
                        summary=f"Simulated NL What-If query: '{query[:100]}'",
                        payload=parsed_q,
                    )
                else:
                    STATE["sim_params"] = {
                        "revenue_change_pct": float(data.get("revenue_change_pct", 0.0)),
                        "delay_days": int(data.get("delay_days", 0)),
                        "fx_shock_pct": float(data.get("fx_shock_pct", 0.0)),
                        "extra_monthly_cost_eur": float(data.get("extra_monthly_cost_eur", 0.0)),
                        "include_commerzbank_actions": bool(data.get("include_commerzbank_actions", True)),
                    }
                _save_state()
            self._send_json(200, get_full_state())
            return

        if path == "/api/actions/decide":
            action_id = str(data.get("action_id", "")).strip()
            decision = str(data.get("decision", "")).strip().lower()
            if decision not in ("approved", "rejected", "snoozed", "pending"):
                self._send_json(400, {"error": "Decision must be approved, rejected, snoozed, or pending"})
                return
            with STATE_LOCK:
                bundle = get_current_bundle()
                target = next((a for a in bundle["actions"] if a["id"] == action_id), None)
                if not target:
                    self._send_json(404, {"error": f"Action '{action_id}' not found"})
                    return

                override: dict[str, Any] = {"status": decision}
                if "amount_eur" in data and data["amount_eur"] is not None:
                    override["amount_eur"] = float(data["amount_eur"])
                if "option" in data and data["option"]:
                    override["selected_option"] = str(data["option"])
                STATE["action_overrides"][action_id] = override

                merged_action = {**target, **override}
                audit_entry = engine.create_audit_entry(
                    STATE["audit_ledger"],
                    actor="HUMAN_OWNER",
                    action_type=f"ACTION_{decision.upper()}",
                    target_id=action_id,
                    summary=f"{decision.title()} '{merged_action['product_name']}' (€{merged_action['amount_eur']:,.2f}).",
                    payload={"option": merged_action["selected_option"], "amount_eur": merged_action["amount_eur"]},
                )
                receipt = None
                if decision == "approved":
                    receipt = engine.create_execution_receipt(merged_action, audit_entry["sha256"])
                    STATE["receipts"].append(receipt)
                _save_state()
            state_out = get_full_state()
            state_out["latest_receipt"] = receipt
            self._send_json(200, state_out)
            return

        if path == "/api/profile":
            allowed_keys = {
                "business_name",
                "owner_name",
                "legal_form",
                "location",
                "industry",
                "employee_count",
                "tax_regime",
                "safety_buffer_eur",
                "context_notes",
            }
            updates = {k: data[k] for k in allowed_keys if k in data}
            with STATE_LOCK:
                STATE["profile_overrides"].update(updates)
                if "safety_buffer_eur" in updates:
                    STATE["preferences"]["safety_buffer_eur"] = float(updates["safety_buffer_eur"])
                engine.create_audit_entry(
                    STATE["audit_ledger"],
                    actor="USER",
                    action_type="PROFILE_UPDATE",
                    target_id="business_profile",
                    summary="Updated business profile & unified AI context notes.",
                    payload=updates,
                )
                _save_state()
            self._send_json(200, get_full_state())
            return

        if path == "/api/preferences":
            with STATE_LOCK:
                prefs = STATE["preferences"]
                for k in ("view_mode", "min_severity", "safety_buffer_eur", "enabled_modules", "muted_categories", "muted_action_ids"):
                    if k in data:
                        prefs[k] = data[k]
                if "safety_buffer_eur" in data:
                    STATE["profile_overrides"]["safety_buffer_eur"] = float(data["safety_buffer_eur"])
                engine.create_audit_entry(
                    STATE["audit_ledger"],
                    actor="USER",
                    action_type="PREFERENCES_UPDATE",
                    target_id="ux_preferences",
                    summary=f"Updated comfort settings (mode={prefs.get('view_mode', 'calm')}).",
                )
                _save_state()
            self._send_json(200, get_full_state())
            return

        if path == "/api/ai/analyze":
            prompt = (
                "Provide a concise 4-bullet executive CFO briefing for the business owner based strictly on the verified KPIs: "
                "(1) Cash & 13-week shortfall risk, (2) FX exposure & Commerzbank hedging option, "
                "(3) Fraud hold / tax compliance priority, and (4) Recommended next approval step."
            )
            ai_res = invoke_cfo_llm(
                [{"role": "user", "content": prompt}],
                include_context=True,
                max_tokens=450,
                allow_fallback=True,
            )
            with STATE_LOCK:
                STATE["ai_briefing"] = {
                    "text": ai_res["reply"],
                    "mode": ai_res["mode"],
                    "model": ai_res["model"],
                    "latency_ms": ai_res["latency_ms"],
                    "llm_error": ai_res["llm_error"],
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                engine.create_audit_entry(
                    STATE["audit_ledger"],
                    actor="OPENSHIFT_AI_LLM",
                    action_type="EXECUTIVE_ANALYSIS",
                    target_id=ai_res["model"],
                    summary=f"Generated CFO executive analysis ({ai_res['mode']}, {ai_res['masked_token_count']} PII tokens masked).",
                )
                _save_state()
            self._send_json(200, get_full_state())
            return

        if path == "/api/chat":
            messages = data.get("messages")
            if not messages and isinstance(data.get("message"), str):
                messages = [{"role": "user", "content": data["message"]}]

            if not isinstance(messages, list) or not messages:
                self._send_json(400, {"error": "Provide a non-empty 'messages' list or 'message' string"})
                return

            include_context = bool(data.get("include_context", False))
            if include_context:
                result = invoke_cfo_llm(messages, include_context=True, max_tokens=512, allow_fallback=True)
                self._send_json(200, result)
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
            return

        self.send_error(404, "Not Found")

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
