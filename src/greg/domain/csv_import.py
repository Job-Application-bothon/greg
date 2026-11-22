"""CSV parsing, column mapping, FX normalization, and hybrid categorization."""
from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from greg.domain.categories import (
    VALID_CATEGORIES,
    classify_category_keywords,
    is_social_security,
)

_CURRENCY_HINT_RE = re.compile(
    r"(?:^|[_\s(\[-])(eur|pln|usd|chf|gbp|czk|sek|nok|dkk|cad|aud|jpy|huf|ron)(?:$|[_\s)\]-])",
    re.IGNORECASE,
)

_DATE_FORMATS = (
    "%d.%m.%y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y-%m",
)

_DATE_CANDIDATES = (
    "booking date",
    "date",
    "value date",
    "transaction date",
    "buchungstag",
    "datum",
    "month",
    "week_ending",
    "week ending",
)
_INCOME_CANDIDATES = ("income", "revenue", "inflow", "sales", "credit", "receipts", "money in")
_EXPENSE_CANDIDATES = (
    "expenses",
    "expense",
    "costs",
    "cost",
    "outflow",
    "spend",
    "debit",
    "money out",
)
_AMOUNT_CANDIDATES = ("amount", "betrag", "value", "sum")
_CURRENCY_CANDIDATES = ("currency", "währung", "curr")
_TYPE_CANDIDATES = ("transaction type", "type", "category", "buchungstext", "art")
_COUNTERPARTY_CANDIDATES = (
    "beneficiary/payer",
    "counterparty",
    "beneficiary",
    "payer",
    "payee",
    "name",
    "empfänger",
    "auftraggeber",
)
_DESCRIPTION_CANDIDATES = (
    "payment reference",
    "reference",
    "description",
    "verwendungszweck",
    "details",
    "info",
)


def parse_date(raw: str) -> date:
    """Parse common European, ISO, US, timestamp, and YYYY-MM date strings."""
    text = raw.strip().strip('"').strip("'")
    if not text:
        raise ValueError("Empty date value")
    # Strip trailing time component if timestamp includes time (e.g. '... 00:00:00' or '...T00:00:00Z')
    head = re.split(r"[T\s]+", text, maxsplit=1)[0]
    if head and (":" in text or "T" in text):
        text = head
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {raw!r}")


def infer_currency_from_headers(*col_names: str | None) -> str | None:
    """Infer an ISO currency code embedded in CSV column names such as `revenue_pln`."""
    for col in col_names:
        if not col:
            continue
        match = _CURRENCY_HINT_RE.search(col)
        if match:
            return match.group(1).upper()
    return None


def parse_amount(raw: str) -> float:
    """Parse EU (`1.234,56`) and US (`1,234.56`) formatted numeric amounts."""
    text = raw.strip().strip('"').strip("'").replace(" ", "")
    if not text:
        raise ValueError("Empty amount")
    text = re.sub(r"[€$£]", "", text)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def detect_delimiter(sample_line: str) -> str:
    """Detect whether the CSV header line uses `;`, `,`, or tab delimiters."""
    counts = {sep: sample_line.count(sep) for sep in (";", ",", "\t")}
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else ";"


def find_column(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    """Match a header name by exact lowercase equality first, then substring containment."""
    lower_map = {h.lower().strip(): h for h in headers}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    for h in headers:
        hl = h.lower().strip()
        for cand in candidates:
            if cand in hl:
                return h
    return None


def _resolve_mapped_column(
    headers: list[str],
    cmap: dict[str, Any],
    key: str,
    fallback_candidates: tuple[str, ...],
) -> str | None:
    if key in cmap:
        raw_val = cmap.get(key)
        if raw_val is None or str(raw_val).strip() == "":
            return None
        col_name = str(raw_val)
        if col_name not in headers:
            raise ValueError(
                f"Mapped column {col_name!r} for '{key}' not found in CSV headers: {headers}"
            )
        return col_name
    return find_column(headers, fallback_candidates)


def parse_csv_transactions(
    csv_text: str,
    column_map: dict[str, Any] | None = None,
    llm_classifier: Callable[[list[dict[str, str]]], dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Parse bank CSV text into normalized transaction records and dominant currency."""
    cleaned = csv_text.lstrip("\ufeff").strip()
    if not cleaned:
        raise ValueError("The uploaded CSV file is empty.")

    lines = cleaned.splitlines()
    delimiter = detect_delimiter(lines[0])
    reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)
    headers = list(reader.fieldnames or [])
    if not headers:
        raise ValueError("Could not read CSV column headers.")

    cmap = column_map if isinstance(column_map, dict) else {}
    date_col = _resolve_mapped_column(headers, cmap, "date", _DATE_CANDIDATES)
    amount_mode = str(cmap.get("amount_mode") or "").strip().lower()

    amount_col: str | None = None
    income_col: str | None = None
    expenses_col: str | None = None

    if amount_mode == "split":
        income_col = _resolve_mapped_column(headers, cmap, "income", _INCOME_CANDIDATES)
        expenses_col = _resolve_mapped_column(headers, cmap, "expenses", _EXPENSE_CANDIDATES)
    elif amount_mode == "single":
        amount_col = _resolve_mapped_column(headers, cmap, "amount", _AMOUNT_CANDIDATES)
    elif amount_mode == "":
        if ("income" in cmap or "expenses" in cmap) and "amount" not in cmap:
            income_col = _resolve_mapped_column(headers, cmap, "income", _INCOME_CANDIDATES)
            expenses_col = _resolve_mapped_column(headers, cmap, "expenses", _EXPENSE_CANDIDATES)
        else:
            amount_col = _resolve_mapped_column(headers, cmap, "amount", _AMOUNT_CANDIDATES)
            if amount_col is None:
                income_col = _resolve_mapped_column(headers, cmap, "income", _INCOME_CANDIDATES)
                expenses_col = _resolve_mapped_column(headers, cmap, "expenses", _EXPENSE_CANDIDATES)
    else:
        raise ValueError(f"Unsupported amount_mode: {amount_mode!r}")

    if not date_col or (amount_col is None and income_col is None and expenses_col is None):
        raise ValueError(
            "CSV must map a Date column and either an Amount column or Income/Expenses columns. "
            f"Found headers: {headers}"
        )

    currency_col = _resolve_mapped_column(headers, cmap, "currency", _CURRENCY_CANDIDATES)
    type_col = _resolve_mapped_column(headers, cmap, "type", _TYPE_CANDIDATES)
    cp_col = _resolve_mapped_column(headers, cmap, "counterparty", _COUNTERPARTY_CANDIDATES)
    ref_col = _resolve_mapped_column(headers, cmap, "description", _DESCRIPTION_CANDIDATES)

    explicit_currency = str(cmap.get("default_currency") or "").strip().upper()
    default_currency = (
        explicit_currency
        or infer_currency_from_headers(amount_col, income_col, expenses_col)
        or "EUR"
    )

    parsed: list[dict[str, Any]] = []
    currencies: list[str] = []
    unique_sigs: dict[str, dict[str, str]] = {}

    def register_tx(
        tx_id: str,
        dt: date,
        orig_amount: float,
        currency: str,
        tx_type: str,
        counterparty: str,
        reference: str,
    ) -> None:
        # Convert foreign currency amounts when an explicit "EUR/<CURR>" quote appears in reference
        # e.g., "4166.67 CHF @ 1.047 EUR/CHF" means 1 EUR = 1.047 CHF => EUR = CHF / 1.047.
        amount_conv = orig_amount
        fx_rate: float | None = None
        if currency != "EUR" and reference:
            match = re.search(
                r"@\s*([0-9]+(?:[.,][0-9]+)?)\s*EUR/" + re.escape(currency),
                reference,
            )
            if match:
                fx_rate = float(match.group(1).replace(",", "."))
                if fx_rate > 0:
                    amount_conv = orig_amount / fx_rate

        resolved_type = tx_type or ("INCOME" if amount_conv >= 0 else "EXPENSE")
        resolved_cp = counterparty or (reference or resolved_type)

        sig_key = f"{resolved_cp}|||{resolved_type}"
        if sig_key not in unique_sigs:
            unique_sigs[sig_key] = {
                "key": sig_key,
                "counterparty": resolved_cp,
                "raw_type": resolved_type,
                "reference": reference,
                "direction": "income" if amount_conv >= 0 else "expense",
            }

        currencies.append("EUR" if fx_rate is not None else currency)
        parsed.append(
            {
                "id": tx_id,
                "date": dt.isoformat(),
                "date_obj": dt,
                "amount": round(amount_conv, 2),
                "original_amount": round(orig_amount, 2),
                "original_currency": currency,
                "fx_rate": fx_rate,
                "raw_type": resolved_type,
                "sig_key": sig_key,
                "category": classify_category_keywords(resolved_type, resolved_cp, reference),
                "counterparty": resolved_cp,
                "description": reference,
                "source": "csv",
            }
        )

    for idx, row in enumerate(reader, start=1):
        raw_date = (row.get(date_col) or "").strip()
        reference = (row.get(ref_col) or "").strip() if ref_col else ""
        base_type = (row.get(type_col) or "").strip() if type_col else ""
        base_cp = (row.get(cp_col) or "").strip() if cp_col else ""
        currency = (
            ((row.get(currency_col) or "").strip().upper() or default_currency)
            if currency_col
            else default_currency
        )

        if amount_col is not None:
            raw_amt = (row.get(amount_col) or "").strip()
            if not raw_date and not raw_amt:
                continue
            dt = parse_date(raw_date)
            orig_amount = parse_amount(raw_amt)
            register_tx(f"csv-{idx}", dt, orig_amount, currency, base_type, base_cp, reference)
        else:
            raw_inc = (row.get(income_col) or "").strip() if income_col else ""
            raw_exp = (row.get(expenses_col) or "").strip() if expenses_col else ""
            if not raw_date and not raw_inc and not raw_exp:
                continue
            dt = parse_date(raw_date)
            inc_val = abs(parse_amount(raw_inc)) if raw_inc else 0.0
            exp_val = abs(parse_amount(raw_exp)) if raw_exp else 0.0
            if inc_val > 0:
                register_tx(
                    f"csv-{idx}-in",
                    dt,
                    inc_val,
                    currency,
                    base_type or "INCOME",
                    base_cp or (income_col or "Income"),
                    reference or (income_col or ""),
                )
            if exp_val > 0:
                register_tx(
                    f"csv-{idx}-out",
                    dt,
                    -exp_val,
                    currency,
                    base_type or "EXPENSE",
                    base_cp or (expenses_col or "Expenses"),
                    reference or (expenses_col or ""),
                )
            if inc_val == 0 and exp_val == 0:
                register_tx(
                    f"csv-{idx}-zero",
                    dt,
                    0.0,
                    currency,
                    base_type or "ZERO",
                    base_cp or "Zero activity",
                    reference,
                )

    if not parsed:
        raise ValueError("No valid transaction rows found in CSV.")

    # Apply optional LLM classification for ambiguous ("Other") signatures while preserving
    # deterministic social-security / insurance keywords as guardrails.
    if llm_classifier is not None and unique_sigs:
        try:
            llm_map = llm_classifier(list(unique_sigs.values())) or {}
        except Exception:
            llm_map = {}
        for item in parsed:
            if item["category"] == "Other" and not is_social_security(
                item["raw_type"], item["counterparty"], item["description"]
            ):
                cand = llm_map.get(item["sig_key"], "")
                if cand in VALID_CATEGORIES:
                    item["category"] = cand

    for item in parsed:
        item.pop("sig_key", None)

    dominant_currency = max(set(currencies), key=currencies.count) if currencies else "EUR"
    return parsed, dominant_currency
