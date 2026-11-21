import json
import os
import socket
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from greg.ledger import LedgerStore, SESSIONS, STORE, VALID_CATEGORIES

STATIC_DIR = Path(__file__).resolve().parent / "static"
SYSTEM_PROMPT = (
    "You are Greg, a friendly, plain-language financial assistant for small business owners. "
    "Use simple everyday words without technical or financial jargon so anyone can understand. "
    "Always answer using the exact numbers provided in the account summary below. "
    "When the user asks about a full year (such as 2025) or the entire dataset, use the "
    "FULL DATASET & YEARLY TOTALS section. When the user asks about a specific month, use that "
    "month's numbers from ALL MONTHS SUMMARY or CURRENTLY SELECTED MONTH. Never confuse a single "
    "month's income with the full year's income."
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
    """Forward chat messages to the configured OpenAI-compatible LLM endpoint (or OpenShift /api/chat relay)."""
    base_url = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("LLM_BASE_URL is not configured")

    configured_model = os.environ.get("LLM_MODEL", "").strip()
    full_messages = list(messages)
    if not full_messages or full_messages[0].get("role") != "system":
        full_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    if base_url.endswith("/api/chat"):
        payload = json.dumps({"messages": full_messages}).encode("utf-8")
        req = urllib.request.Request(
            base_url,
            data=payload,
            headers=_llm_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if "reply" not in body:
            raise ValueError("Missing reply from OpenShift /api/chat relay")
        return {
            "reply": str(body["reply"]),
            "model": str(body.get("model") or configured_model or "llama-32-3b-instruct"),
        }

    model = _resolve_model(base_url, configured_model)
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


def _classify_with_llm(signatures: list[dict[str, str]]) -> dict[str, str]:
    """Use the configured OpenShift LLM to classify unique CSV signatures into Salaries, Taxes, or Other."""
    if not os.environ.get("LLM_BASE_URL", "").strip() or not signatures:
        return {}

    capped = signatures[:60]
    lines = [
        f'{idx}: counterparty="{s["counterparty"]}" | type="{s["raw_type"]}" | direction="{s["direction"]}" | ref="{s["reference"][:80]}"'
        for idx, s in enumerate(capped)
    ]
    prompt = (
        "Classify each bank transaction signature into exactly one category: "
        '"Salaries", "Taxes", or "Other".\n'
        "- Salaries: employee or owner wages, payroll, salary payments.\n"
        "- Taxes: direct payments to tax authorities (income tax, wage tax, trade tax, VAT). "
        "Note: health insurance or social security contributions are Other, not Taxes.\n"
        "- Other: everything else (sales, rent, suppliers, utilities, insurance, transfers).\n\n"
        "Respond ONLY with a valid JSON object mapping the numeric index string to the category, "
        'e.g. {"0": "Other", "1": "Salaries"}.\n\n'
        + "\n".join(lines)
    )
    try:
        res = call_llm(
            [
                {"role": "system", "content": "You are a strict JSON transaction classifier."},
                {"role": "user", "content": prompt},
            ]
        )
        raw = res.get("reply", "").strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, dict):
            return {}
        mapping: dict[str, str] = {}
        for k, v in parsed.items():
            try:
                idx = int(k)
            except (ValueError, TypeError):
                continue
            if 0 <= idx < len(capped) and v in VALID_CATEGORIES:
                mapping[capped[idx]["key"]] = v
        return mapping
    except Exception:
        return {}


def _parse_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_int_or_default(value: Any, default: int = 30) -> int:
    parsed = _parse_optional_int(value)
    return default if parsed is None else parsed


def _build_context_system_prompt(
    selected_month: str | None = None,
    horizon_days: int = 30,
    horizon_months: int | None = None,
    store: LedgerStore | None = None,
) -> str:
    active_store = store if store is not None else STORE
    view = active_store.get_view(
        selected_month=selected_month,
        horizon_days=horizon_days,
        horizon_months=horizon_months,
    )
    if not view.get("has_data"):
        return SYSTEM_PROMPT

    cur = view.get("currency", "EUR")
    sym = "€" if cur == "EUR" else f"{cur} "
    ds = view["dataset_summary"]
    ms = view["month_summary"]
    la = view["liquidity_agent"]

    yearly_lines = []
    for y in view["yearly_summaries"]:
        ce = y["category_expenses"]
        yearly_lines.append(
            f"- Year {y['year']}: Total Income = {sym}{y['total_income']:,.2f} | "
            f"Total Expenses = {sym}{y['total_expenses']:,.2f} | "
            f"Total Profit = {sym}{y['profit']:,.2f} "
            f"(Expenses -> Salaries: {sym}{ce['Salaries']:,.2f}, Taxes: {sym}{ce['Taxes']:,.2f}, Other: {sym}{ce['Other']:,.2f})"
        )

    monthly_lines = [
        f"- {m['month']} ({m['label']}): Income {sym}{m['total_income']:,.2f} | "
        f"Expenses {sym}{m['total_expenses']:,.2f} | Profit {sym}{m['profit']:,.2f} | "
        f"End Balance {sym}{m['end_balance']:,.2f}"
        for m in view["all_months_summary"]
    ]

    cat_lines = []
    for cat in view["expense_breakdown"]:
        top_people = ", ".join(
            f"{p['name']}: {sym}{p['total']:,.2f}" for p in cat["people"][:6]
        )
        cat_lines.append(f"  - {cat['category']}: {sym}{cat['total']:,.2f} ({top_people})")

    if la.get("engine") == "greg_planner":
        pm = la.get("planner_meta") or {}
        notes_preview = "\n".join(f"  - {n}" for n in (pm.get("notes") or [])[:6])
        forecast_section = (
            f"\n\nSEASONAL MONTHLY FORECAST (next {la.get('horizon_months', 12)} months after {la.get('last_history_month')}):\n"
            f"- Engine: greg_planner (Inflow: {pm.get('inflow_method_label')}; Outflow: {pm.get('outflow_method_label')})\n"
            f"- Total expected income: {sym}{la['total_expected_in']:,.2f} | Total expected expenses: {sym}{la['total_expected_out']:,.2f}\n"
            f"- Lowest expected balance: {sym}{la['lowest_predicted_balance']:,.2f} in {la['lowest_predicted_date']}\n"
            f"- Lowest pessimistic stress-test balance: {sym}{la.get('lowest_pessimistic_balance', 0):,.2f} in {la.get('lowest_pessimistic_date')}\n"
            f"- Drops below zero: {'YES' if la['goes_below_zero'] else 'NO'}\n"
            f"- Recommended short-term bank credit needed: {sym}{la['recommended_credit_amount']:,.2f}\n"
            f"- Planner notes:\n{notes_preview if notes_preview else '  - None'}\n"
        )
    else:
        bills_preview = ", ".join(
            f"{b['counterparty']} ({b['amount']:+.2f} on {b['date']})"
            for b in la["upcoming_bills"][:8]
        ) or "None detected"
        forecast_section = (
            f"\n\nFORECAST (next {la['horizon_days']} days after {la['as_of_date']}):\n"
            f"- Lowest predicted balance: {sym}{la['lowest_predicted_balance']:,.2f} on {la['lowest_predicted_date']}\n"
            f"- Drops below zero: {'YES' if la['goes_below_zero'] else 'NO'}\n"
            f"- Recommended short-term bank credit needed: {sym}{la['recommended_credit_amount']:,.2f}\n"
            f"- Upcoming regular bills in forecast window: {bills_preview}\n"
        )

    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"FULL DATASET & YEARLY TOTALS ({ds['first_date']} to {ds['last_date']}):\n"
        f"- Overall Dataset: Total Income = {sym}{ds['total_income']:,.2f} | "
        f"Total Expenses = {sym}{ds['total_expenses']:,.2f} | "
        f"Total Profit = {sym}{ds['profit']:,.2f} | "
        f"Starting Balance = {sym}{ds['start_balance']:,.2f} | Final Balance = {sym}{ds['end_balance']:,.2f}\n"
        + "\n".join(yearly_lines)
        + "\n\nALL MONTHS SUMMARY:\n"
        + "\n".join(monthly_lines)
        + f"\n\nCURRENTLY SELECTED MONTH ({view['selected_month_label']}):\n"
        f"- Income: {sym}{ms['total_income']:,.2f}\n"
        f"- Expenses: {sym}{ms['total_expenses']:,.2f}\n"
        f"- Profit: {sym}{ms['profit']:,.2f}\n"
        f"- Balance at end of {view['selected_month_label']}: {sym}{ms['end_balance']:,.2f}\n"
        f"- Expenses by category in {view['selected_month_label']}:\n"
        + ("\n".join(cat_lines) if cat_lines else "  - None")
        + forecast_section
    )


class Handler(BaseHTTPRequestHandler):
    """HTTP handler serving the frontend, ledger/liquidity endpoints, health check, and LLM proxy."""

    def _get_store(self) -> LedgerStore:
        sid = self.headers.get("X-Greg-Session", "").strip()
        return SESSIONS.get(sid or None)

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self._send_json(200, _health_payload())
            return

        if path == "/api/view":
            qs = parse_qs(parsed.query)
            month = qs.get("month", [None])[0]
            horizon = _parse_int_or_default(qs.get("horizon", ["30"])[0], 30)
            horizon_months = _parse_optional_int(qs.get("horizon_months", [None])[0])
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
            font_path = STATIC_DIR / "InterVariable.woff2"
            if font_path.exists():
                font_bytes = font_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "font/woff2")
                self.send_header("Content-Length", str(len(font_bytes)))
                self.end_headers()
                self.wfile.write(font_bytes)
                return

        if path in ("/", "/index.html"):
            html = (STATIC_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            data = self._read_json()
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid JSON request: {exc}"})
            return

        store = self._get_store()

        if path == "/api/import":
            csv_text = data.get("csv_text", "")
            filename = str(data.get("filename") or "statement.csv")
            column_map = data.get("column_map")
            if column_map is not None and not isinstance(column_map, dict):
                self._send_json(400, {"error": "'column_map' must be a JSON object"})
                return
            try:
                opening_balance = float(data.get("opening_balance", 0.0))
                horizon = _parse_int_or_default(data.get("horizon_days"), 30)
                horizon_months = _parse_optional_int(data.get("horizon_months"))
                keep_manual = bool(data.get("keep_manual", False))
                store.import_csv(
                    csv_text=csv_text,
                    filename=filename,
                    opening_balance=opening_balance,
                    keep_manual=keep_manual,
                    llm_classifier=_classify_with_llm,
                    column_map=column_map,
                )
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, store.get_view(horizon_days=horizon, horizon_months=horizon_months))
            return

        if path == "/api/operations":
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
            horizon = _parse_int_or_default(data.get("horizon_days"), 30)
            horizon_months = _parse_optional_int(data.get("horizon_months"))
            self._send_json(
                200,
                store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months),
            )
            return

        if path == "/api/operations/delete":
            op_id = str(data.get("id") or "")
            store.delete_operation(op_id)
            month = data.get("selected_month")
            horizon = _parse_int_or_default(data.get("horizon_days"), 30)
            horizon_months = _parse_optional_int(data.get("horizon_months"))
            self._send_json(
                200,
                store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months),
            )
            return

        if path == "/api/opening-balance":
            try:
                store.set_opening_balance(float(data.get("opening_balance", 0.0)))
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            month = data.get("selected_month")
            horizon = _parse_int_or_default(data.get("horizon_days"), 30)
            horizon_months = _parse_optional_int(data.get("horizon_months"))
            self._send_json(
                200,
                store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months),
            )
            return

        if path == "/api/approve-credit":
            try:
                amount = float(data.get("amount", 0.0))
                month = str(data.get("selected_month") or "")
                horizon = _parse_int_or_default(data.get("horizon_days"), 30)
                horizon_months = _parse_optional_int(data.get("horizon_months"))
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
            return

        if path == "/api/reset":
            store.reset()
            self._send_json(200, store.get_view())
            return

        if path == "/api/explain":
            month = data.get("selected_month")
            horizon = _parse_int_or_default(data.get("horizon_days"), 30)
            horizon_months = _parse_optional_int(data.get("horizon_months"))
            view = store.get_view(selected_month=month, horizon_days=horizon, horizon_months=horizon_months)
            if not view.get("has_data"):
                self._send_json(400, {"error": "Import a CSV or add an operation first."})
                return
            la = view["liquidity_agent"]
            horizon_label = (
                f"next {la.get('horizon_months', 12)} months"
                if la.get("engine") == "greg_planner"
                else f"next {la['horizon_days']} days"
            )
            prompt = (
                f"Please explain my cash situation at the end of {view['selected_month_label']} "
                f"and for the {horizon_label} in 3 short, simple bullet points "
                f"using everyday language (no financial jargon). State clearly if my balance stays safe "
                f"or drops below zero, and note the exact credit amount "
                f"(€{la['recommended_credit_amount']:,.2f}) I should ask my bank for if needed."
            )
            sys_prompt = _build_context_system_prompt(
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
                self._send_json(200, {"reply": fallback, "model": "built-in-summary", "source": "fallback"})
            return

        if path == "/api/chat":
            messages = data.get("messages")
            if not messages and isinstance(data.get("message"), str):
                messages = [{"role": "user", "content": data["message"]}]

            if not isinstance(messages, list) or not messages:
                self._send_json(
                    400, {"error": "Provide a non-empty 'messages' list or 'message' string"}
                )
                return

            # If store has data and caller didn't supply a custom system message, prepend context
            call_messages = list(messages)
            if store.transactions and call_messages[0].get("role") != "system":
                month = data.get("selected_month")
                horizon = _parse_int_or_default(data.get("horizon_days"), 30)
                horizon_months = _parse_optional_int(data.get("horizon_months"))
                call_messages.insert(
                    0,
                    {
                        "role": "system",
                        "content": _build_context_system_prompt(
                            selected_month=month,
                            horizon_days=horizon,
                            horizon_months=horizon_months,
                            store=store,
                        ),
                    },
                )

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
