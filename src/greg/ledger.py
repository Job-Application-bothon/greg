from __future__ import annotations

import csv
import io
import math
import re
import statistics
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

VALID_CATEGORIES = ("Salaries", "Taxes", "Other")


def classify_category_keywords(raw_type: str, counterparty: str, reference: str) -> str:
    """Deterministically classify a transaction into Salaries, Taxes, or Other."""
    text = f"{raw_type} {counterparty} {reference}".lower()
    raw_lower = raw_type.lower().strip()

    # Social security / health insurance funds belong under Other, not Taxes.
    if any(
        kw in text
        for kw in (
            "krankenkasse",
            "social security",
            "social health",
            "health insurance",
            "pension insurance",
            "sozialversicherung",
        )
    ):
        return "Other"

    tax_phrases = (
        "tax office",
        "finanzamt",
        "wage tax",
        "income tax",
        "trade tax",
        "corporate tax",
        "sales tax",
        "lohnsteuer",
        "einkommensteuer",
        "gewerbesteuer",
        "umsatzsteuer",
        "körperschaftsteuer",
        "solidarity surcharge",
        "solidaritätszuschlag",
        "tax prepayment",
        "tax transfer",
        "tax payment",
        "urząd skarbowy",
        "hmrc",
        "irs ",
    )
    if (
        raw_lower in ("tax", "taxes", "tax office transfer")
        or any(phrase in text for phrase in tax_phrases)
        or re.search(r"\b(tax|taxes|vat|mwst|ust|steuer|steuern)\b", text)
    ):
        return "Taxes"

    salary_phrases = (
        "salary",
        "salaries",
        "payroll",
        "gehalt",
        "gehälter",
        "lohn",
        "wages",
        "net pay",
        "staff pay",
    )
    if (
        raw_lower in ("salary", "salaries", "salary payment", "payroll")
        or any(phrase in text for phrase in salary_phrases)
    ):
        return "Salaries"

    return "Other"


def normalize_category(
    cat: str, raw_type: str = "", counterparty: str = "", reference: str = ""
) -> str:
    """Normalize a category string strictly to Salaries, Taxes, or Other."""
    c = (cat or "").strip().lower()
    if c in ("salaries", "salary"):
        return "Salaries"
    if c in ("taxes", "tax"):
        return "Taxes"
    if c == "other":
        return "Other"
    return classify_category_keywords(raw_type or cat, counterparty, reference)


def _parse_date(raw: str) -> date:
    text = raw.strip().strip('"').strip("'")
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {raw!r}")


def _parse_amount(raw: str) -> float:
    text = raw.strip().strip('"').strip("'").replace(" ", "")
    if not text:
        raise ValueError("Empty amount")
    # Remove currency symbols if embedded
    text = re.sub(r"[€$£]", "", text)
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            # German format: 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:
            # English format: 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def _detect_delimiter(sample_line: str) -> str:
    counts = {sep: sample_line.count(sep) for sep in (";", ",", "\t")}
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else ";"


def _find_column(headers: list[str], candidates: tuple[str, ...]) -> str | None:
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


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_label(ym: str) -> str:
    year, month = (int(x) for x in ym.split("-"))
    names = [
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    return f"{names[month]} {year}"


class LedgerStore:
    """In-memory zero-hardcoding store for imported CSV and manual operations."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.transactions: list[dict[str, Any]] = []
        self.opening_balance: float = 0.0
        self.filename: str | None = None
        self.account_currency: str = "EUR"
        self.approved_credit: dict[str, Any] | None = None

    def import_csv(
        self,
        csv_text: str,
        filename: str = "uploaded.csv",
        opening_balance: float = 0.0,
        keep_manual: bool = False,
        llm_classifier: Callable[[list[dict[str, str]]], dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        cleaned = csv_text.lstrip("\ufeff").strip()
        if not cleaned:
            raise ValueError("The uploaded CSV file is empty.")

        lines = cleaned.splitlines()
        delimiter = _detect_delimiter(lines[0])
        reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)
        headers = list(reader.fieldnames or [])
        if not headers:
            raise ValueError("Could not read CSV column headers.")

        date_col = _find_column(
            headers, ("booking date", "date", "value date", "transaction date", "buchungstag", "datum")
        )
        amount_col = _find_column(headers, ("amount", "betrag", "value", "sum"))
        currency_col = _find_column(headers, ("currency", "währung", "curr"))
        type_col = _find_column(
            headers, ("transaction type", "type", "category", "buchungstext", "art")
        )
        cp_col = _find_column(
            headers,
            (
                "beneficiary/payer",
                "counterparty",
                "beneficiary",
                "payer",
                "payee",
                "name",
                "empfänger",
                "auftraggeber",
            ),
        )
        ref_col = _find_column(
            headers,
            ("payment reference", "reference", "description", "verwendungszweck", "details", "info"),
        )

        if not date_col or not amount_col:
            raise ValueError(
                f"CSV must contain at least a Date column and an Amount column. Found headers: {headers}"
            )

        manual_existing = (
            [t for t in self.transactions if t.get("source") == "manual"] if keep_manual else []
        )

        parsed: list[dict[str, Any]] = []
        currencies: list[str] = []
        unique_sigs: dict[str, dict[str, str]] = {}

        for idx, row in enumerate(reader, start=1):
            raw_date = (row.get(date_col) or "").strip()
            raw_amt = (row.get(amount_col) or "").strip()
            if not raw_date and not raw_amt:
                continue
            dt = _parse_date(raw_date)
            orig_amount = _parse_amount(raw_amt)
            currency = (row.get(currency_col) or "EUR").strip().upper() if currency_col else "EUR"
            reference = (row.get(ref_col) or "").strip() if ref_col else ""
            tx_type = (row.get(type_col) or "").strip() if type_col else ""
            counterparty = (row.get(cp_col) or "").strip() if cp_col else ""

            # Convert foreign currency amounts when an explicit "EUR/<CURR>" quote appears in reference
            # e.g., "4166.67 CHF @ 1.047 EUR/CHF" means 1 EUR = 1.047 CHF => EUR = CHF / 1.047
            amount_eur = orig_amount
            fx_rate: float | None = None
            if currency != "EUR" and reference:
                match = re.search(
                    r"@\s*([0-9]+(?:[.,][0-9]+)?)\s*EUR/" + re.escape(currency), reference
                )
                if match:
                    fx_rate = float(match.group(1).replace(",", "."))
                    if fx_rate > 0:
                        amount_eur = orig_amount / fx_rate

            if not tx_type:
                tx_type = "INCOME" if amount_eur >= 0 else "EXPENSE"
            if not counterparty:
                counterparty = reference or tx_type

            sig_key = f"{counterparty}|||{tx_type}"
            if sig_key not in unique_sigs:
                unique_sigs[sig_key] = {
                    "key": sig_key,
                    "counterparty": counterparty,
                    "raw_type": tx_type,
                    "reference": reference,
                    "direction": "income" if amount_eur >= 0 else "expense",
                }

            currencies.append("EUR" if fx_rate is not None else currency)
            parsed.append(
                {
                    "id": f"csv-{idx}",
                    "date": dt.isoformat(),
                    "date_obj": dt,
                    "amount": round(amount_eur, 2),
                    "original_amount": round(orig_amount, 2),
                    "original_currency": currency,
                    "fx_rate": fx_rate,
                    "raw_type": tx_type,
                    "sig_key": sig_key,
                    "category": classify_category_keywords(tx_type, counterparty, reference),
                    "counterparty": counterparty,
                    "description": reference,
                    "source": "csv",
                }
            )

        if not parsed:
            raise ValueError("No valid transaction rows found in CSV.")

        # Apply optional LLM classification for ambiguous ("Other") signatures, while preserving
        # explicit deterministic tax/salary/social-security rules as guardrails.
        if llm_classifier is not None and unique_sigs:
            try:
                llm_map = llm_classifier(list(unique_sigs.values())) or {}
            except Exception:
                llm_map = {}
            for item in parsed:
                kw_cat = item["category"]
                if kw_cat == "Other":
                    # Do not override explicit social security / insurance keywords into Taxes
                    combined = f"{item['raw_type']} {item['counterparty']} {item['description']}".lower()
                    is_social = any(
                        kw in combined
                        for kw in (
                            "krankenkasse",
                            "social security",
                            "social health",
                            "health insurance",
                            "pension insurance",
                            "sozialversicherung",
                        )
                    )
                    if not is_social:
                        cand = llm_map.get(item["sig_key"], "")
                        if cand in VALID_CATEGORIES:
                            item["category"] = cand

        for item in parsed:
            item.pop("sig_key", None)

        self.transactions = parsed + manual_existing
        self.transactions.sort(key=lambda t: (t["date"], t["id"]))
        self.opening_balance = float(opening_balance)
        self.filename = filename
        self.approved_credit = None
        if currencies:
            self.account_currency = max(set(currencies), key=currencies.count)

        return {
            "imported_rows": len(parsed),
            "filename": self.filename,
            "opening_balance": self.opening_balance,
            "first_date": self.transactions[0]["date"],
            "last_date": self.transactions[-1]["date"],
        }

    def add_manual_operation(
        self,
        date_str: str,
        kind: str,
        amount: float,
        counterparty: str,
        category: str = "Other",
        description: str = "",
    ) -> dict[str, Any]:
        dt = _parse_date(date_str)
        raw_val = abs(float(amount))
        if raw_val <= 0:
            raise ValueError("Amount must be greater than zero.")

        is_income = kind.strip().lower() == "income"
        signed_amount = round(raw_val if is_income else -raw_val, 2)
        clean_cp = counterparty.strip() or ("Manual Income" if is_income else "Manual Expense")
        clean_cat = normalize_category(category, category, clean_cp, description)
        clean_desc = description.strip() or f"{clean_cat} - {clean_cp}"

        op = {
            "id": f"manual-{uuid.uuid4().hex[:8]}",
            "date": dt.isoformat(),
            "date_obj": dt,
            "amount": signed_amount,
            "original_amount": signed_amount,
            "original_currency": self.account_currency,
            "fx_rate": None,
            "raw_type": clean_cat.upper(),
            "category": clean_cat,
            "counterparty": clean_cp,
            "description": clean_desc,
            "source": "manual",
        }
        self.transactions.append(op)
        self.transactions.sort(key=lambda t: (t["date"], t["id"]))
        return self._serialize_tx(op)

    def delete_operation(self, op_id: str) -> bool:
        before = len(self.transactions)
        self.transactions = [
            t for t in self.transactions if not (t["id"] == op_id and t.get("source") == "manual")
        ]
        return len(self.transactions) < before

    def set_opening_balance(self, amount: float) -> None:
        self.opening_balance = round(float(amount), 2)

    def approve_credit(self, amount: float, month: str, horizon_days: int) -> dict[str, Any]:
        val = round(max(0.0, float(amount)), 2)
        if val <= 0:
            self.approved_credit = None
        else:
            self.approved_credit = {
                "amount": val,
                "month": month,
                "horizon_days": int(horizon_days),
                "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "mode": "simulated",
            }
        return self.approved_credit or {"amount": 0.0, "mode": "none"}

    @staticmethod
    def _serialize_tx(tx: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": tx["id"],
            "date": tx["date"],
            "amount": round(tx["amount"], 2),
            "original_amount": tx["original_amount"],
            "original_currency": tx["original_currency"],
            "fx_rate": tx["fx_rate"],
            "category": tx["category"],
            "counterparty": tx["counterparty"],
            "description": tx["description"],
            "source": tx["source"],
        }

    def get_view(self, selected_month: str | None = None, horizon_days: int = 30) -> dict[str, Any]:
        horizon_days = max(7, min(180, int(horizon_days)))
        if not self.transactions:
            return {
                "has_data": False,
                "filename": None,
                "opening_balance": self.opening_balance,
                "currency": self.account_currency,
                "months": [],
                "selected_month": None,
            }

        months_set = sorted({_month_key(t["date_obj"]) for t in self.transactions})
        if not selected_month or selected_month not in months_set:
            selected_month = months_set[-1]

        # Build daily totals across entire ledger
        all_dates = [t["date_obj"] for t in self.transactions]
        min_date = min(all_dates)
        max_date = max(all_dates)

        tx_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        tx_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
        tx_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for t in self.transactions:
            tx_by_date[t["date_obj"]].append(t)
            tx_by_month[_month_key(t["date_obj"])].append(t)
            tx_by_year[t["date_obj"].year].append(t)

        # Determine end date of selected_month
        sel_year, sel_m = (int(x) for x in selected_month.split("-"))
        first_of_month = date(sel_year, sel_m, 1)
        if sel_m == 12:
            next_month_first = date(sel_year + 1, 1, 1)
        else:
            next_month_first = date(sel_year, sel_m + 1, 1)
        last_of_month = next_month_first - timedelta(days=1)

        # Calculate running balance day by day from min_date through max(last_of_month, max_date)
        end_walk = max(last_of_month, max_date)
        running_bal = self.opening_balance
        daily_balances: dict[date, float] = {}
        cur = min_date
        while cur <= end_walk:
            day_sum = sum(t["amount"] for t in tx_by_date.get(cur, []))
            running_bal = round(running_bal + day_sum, 2)
            daily_balances[cur] = running_bal
            cur += timedelta(days=1)

        # Balance right before first_of_month
        prev_day = first_of_month - timedelta(days=1)
        month_start_balance = (
            daily_balances[prev_day] if prev_day >= min_date else round(self.opening_balance, 2)
        )
        month_end_balance = daily_balances.get(last_of_month, month_start_balance)

        # Selected month transactions & lowest daily balance inside the month
        month_txs = [
            t for t in self.transactions if first_of_month <= t["date_obj"] <= last_of_month
        ]
        total_income = round(sum(t["amount"] for t in month_txs if t["amount"] > 0), 2)
        total_expenses = round(sum(abs(t["amount"]) for t in month_txs if t["amount"] < 0), 2)
        profit = round(total_income - total_expenses, 2)

        lowest_in_month = month_start_balance
        lowest_in_month_date = first_of_month.isoformat()
        d_ptr = first_of_month
        while d_ptr <= last_of_month:
            b = daily_balances.get(d_ptr, month_start_balance)
            if b < lowest_in_month or d_ptr == first_of_month:
                lowest_in_month = b
                lowest_in_month_date = d_ptr.isoformat()
            d_ptr += timedelta(days=1)

        income_breakdown = self._build_breakdown([t for t in month_txs if t["amount"] > 0], is_expense=False)
        expense_breakdown = self._build_breakdown([t for t in month_txs if t["amount"] < 0], is_expense=True)

        # Full-dataset & per-year summaries + all-months table (for accurate LLM answers)
        yearly_summaries: list[dict[str, Any]] = []
        for yr in sorted(tx_by_year):
            y_txs = tx_by_year[yr]
            y_inc = round(sum(t["amount"] for t in y_txs if t["amount"] > 0), 2)
            y_exp = round(sum(abs(t["amount"]) for t in y_txs if t["amount"] < 0), 2)
            y_prof = round(y_inc - y_exp, 2)
            cat_exp = {
                cat: round(
                    sum(abs(t["amount"]) for t in y_txs if t["amount"] < 0 and t["category"] == cat),
                    2,
                )
                for cat in VALID_CATEGORIES
            }
            yearly_summaries.append(
                {
                    "year": yr,
                    "total_income": y_inc,
                    "total_expenses": y_exp,
                    "profit": y_prof,
                    "transaction_count": len(y_txs),
                    "category_expenses": cat_exp,
                }
            )

        ds_income = round(sum(t["amount"] for t in self.transactions if t["amount"] > 0), 2)
        ds_expenses = round(sum(abs(t["amount"]) for t in self.transactions if t["amount"] < 0), 2)
        dataset_summary = {
            "first_date": min_date.isoformat(),
            "last_date": max_date.isoformat(),
            "total_income": ds_income,
            "total_expenses": ds_expenses,
            "profit": round(ds_income - ds_expenses, 2),
            "start_balance": round(self.opening_balance, 2),
            "end_balance": daily_balances.get(max_date, round(self.opening_balance, 2)),
            "transaction_count": len(self.transactions),
        }

        all_months_summary: list[dict[str, Any]] = []
        for mk in months_set:
            m_y, m_m = (int(x) for x in mk.split("-"))
            m_next = date(m_y + 1, 1, 1) if m_m == 12 else date(m_y, m_m + 1, 1)
            m_last = m_next - timedelta(days=1)
            m_txs = tx_by_month[mk]
            m_inc = round(sum(t["amount"] for t in m_txs if t["amount"] > 0), 2)
            m_exp = round(sum(abs(t["amount"]) for t in m_txs if t["amount"] < 0), 2)
            all_months_summary.append(
                {
                    "month": mk,
                    "label": _month_label(mk),
                    "total_income": m_inc,
                    "total_expenses": m_exp,
                    "profit": round(m_inc - m_exp, 2),
                    "end_balance": daily_balances.get(m_last, round(self.opening_balance, 2)),
                }
            )

        # Historical chart points (last 60 days up to last_of_month, or from min_date)
        hist_start = max(min_date, last_of_month - timedelta(days=60))
        history_series: list[dict[str, Any]] = []
        d_ptr = hist_start
        while d_ptr <= last_of_month:
            history_series.append(
                {
                    "date": d_ptr.isoformat(),
                    "balance": daily_balances.get(d_ptr, self.opening_balance),
                }
            )
            d_ptr += timedelta(days=1)

        # Run the Basic Liquidity Agent forecast for the next `horizon_days` starting after `last_of_month`
        forecast = self._run_liquidity_forecast(
            as_of_date=last_of_month,
            current_balance=month_end_balance,
            horizon_days=horizon_days,
        )

        return {
            "has_data": True,
            "filename": self.filename,
            "opening_balance": self.opening_balance,
            "currency": self.account_currency,
            "total_transactions": len(self.transactions),
            "months": [{"key": m, "label": _month_label(m)} for m in months_set],
            "selected_month": selected_month,
            "selected_month_label": _month_label(selected_month),
            "month_summary": {
                "start_balance": month_start_balance,
                "end_balance": month_end_balance,
                "total_income": total_income,
                "total_expenses": total_expenses,
                "net_change": profit,
                "profit": profit,
                "lowest_balance": lowest_in_month,
                "lowest_balance_date": lowest_in_month_date,
                "transaction_count": len(month_txs),
            },
            "yearly_summaries": yearly_summaries,
            "dataset_summary": dataset_summary,
            "all_months_summary": all_months_summary,
            "income_breakdown": income_breakdown,
            "expense_breakdown": expense_breakdown,
            "month_transactions": [
                self._serialize_tx(t)
                for t in sorted(month_txs, key=lambda x: (x["date"], x["id"]), reverse=True)
            ],
            "history_series": history_series,
            "liquidity_agent": forecast,
            "approved_credit": self.approved_credit,
        }

    def _build_breakdown(
        self, txs: list[dict[str, Any]], is_expense: bool
    ) -> list[dict[str, Any]]:
        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in txs:
            by_cat[t["category"]].append(t)

        result: list[dict[str, Any]] = []
        for cat, items in by_cat.items():
            cat_total = round(sum(abs(i["amount"]) if is_expense else i["amount"] for i in items), 2)
            by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for i in items:
                by_person[i["counterparty"]].append(i)

            people_list: list[dict[str, Any]] = []
            for person, p_items in by_person.items():
                p_total = round(
                    sum(abs(i["amount"]) if is_expense else i["amount"] for i in p_items), 2
                )
                people_list.append(
                    {
                        "name": person,
                        "total": p_total,
                        "count": len(p_items),
                        "items": [
                            {
                                "id": i["id"],
                                "date": i["date"],
                                "amount": round(i["amount"], 2),
                                "description": i["description"],
                                "source": i["source"],
                            }
                            for i in sorted(p_items, key=lambda x: x["date"])
                        ],
                    }
                )
            people_list.sort(key=lambda p: p["total"], reverse=True)
            result.append(
                {
                    "category": cat,
                    "total": cat_total,
                    "count": len(items),
                    "people": people_list,
                }
            )

        result.sort(key=lambda c: c["total"], reverse=True)
        return result

    def _run_liquidity_forecast(
        self,
        as_of_date: date,
        current_balance: float,
        horizon_days: int,
    ) -> dict[str, Any]:
        """
        Pure statistical & rule-free cash-flow forecaster (zero hardcoding):
        1. Detects recurring monthly and quarterly payments from history up to `as_of_date`.
        2. Computes average weekday variable cash inflows and outflows from recent non-recurring history.
        3. Overlays any future manual operations entered by the user inside the forecast window.
        4. Projects daily balance `horizon_days` ahead and computes exact credit requirement if balance < 0.
        """
        history = [t for t in self.transactions if t["date_obj"] <= as_of_date]
        distinct_hist_months = sorted({_month_key(t["date_obj"]) for t in history})
        num_hist_months = max(1, len(distinct_hist_months))

        # Step 1: Automatically detect recurring monthly / quarterly payments
        # Group by (counterparty, raw_type) so separate schedules with the same counterparty stay distinct.
        raw_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for t in history:
            raw_groups[(t["counterparty"], t.get("raw_type") or t["category"])].append(t)

        recurring_tx_ids: set[str] = set()
        recurring_schedules: list[dict[str, Any]] = []

        for (cp, raw_tp), items in raw_groups.items():
            item_months = {_month_key(i["date_obj"]) for i in items}
            # A regular bill/payment occurs at most ~once per active month (not daily/weekly)
            if len(items) > len(item_months) * 1.35:
                continue

            # Split sub-schedules if the same counterparty has payments on clearly different days of month
            sub_buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for i in items:
                bucket_key = round(i["date_obj"].day / 5) * 5 if len(items) < 10 else 0
                sub_buckets[bucket_key].append(i)

            for _, s_items in sub_buckets.items():
                s_months = sorted({(i["date_obj"].year, i["date_obj"].month) for i in s_items})
                days = [i["date_obj"].day for i in s_items]
                amts = [i["amount"] for i in s_items]
                mean_amt = statistics.mean(amts)
                if mean_amt == 0:
                    continue

                day_std = statistics.pstdev(days) if len(days) > 1 else 0.0
                amt_cv = (statistics.pstdev(amts) / abs(mean_amt)) if len(amts) > 1 else 0.0
                if day_std > 4.5 or amt_cv > 0.35:
                    continue

                # Determine cadence: monthly vs quarterly
                month_indices = [y * 12 + m for (y, m) in s_months]
                gaps = [b - a for a, b in zip(month_indices, month_indices[1:])]

                cadence: str | None = None
                if len(s_items) >= 2 and gaps and all(g == 1 for g in gaps):
                    cadence = "monthly"
                elif len(s_items) >= 2 and sum(1 for g in gaps if g == 1) >= len(gaps) * 0.6:
                    cadence = "monthly"
                elif len(s_items) >= 2 and gaps and all(g == 3 for g in gaps):
                    cadence = "quarterly"
                elif num_hist_months == 1 and len(s_items) == 1 and abs(mean_amt) >= 300:
                    # When only 1 month of history is loaded, single fixed bills >= 300 are treated as monthly
                    cadence = "monthly"

                if cadence:
                    for i in s_items:
                        recurring_tx_ids.add(i["id"])
                    typical_day = max(1, min(28, round(statistics.mean(days))))
                    last_seen_ym = s_months[-1]
                    recurring_schedules.append(
                        {
                            "counterparty": cp,
                            "category": s_items[0]["category"],
                            "typical_day": typical_day,
                            "expected_amount": round(mean_amt, 2),
                            "cadence": cadence,
                            "last_year": last_seen_ym[0],
                            "last_month": last_seen_ym[1],
                        }
                    )

        # Step 2: Variable daily cash-flow baseline (non-recurring transactions from recent active months)
        var_txs = [t for t in history if t["id"] not in recurring_tx_ids]
        active_var_months = sorted({_month_key(t["date_obj"]) for t in var_txs})
        recent_active_months = set(active_var_months[-2:]) if active_var_months else set()

        var_by_day_in: dict[date, float] = defaultdict(float)
        var_by_day_out: dict[date, float] = defaultdict(float)
        for t in var_txs:
            d = t["date_obj"]
            if _month_key(d) in recent_active_months:
                if t["amount"] >= 0:
                    var_by_day_in[d] += t["amount"]
                else:
                    var_by_day_out[d] += t["amount"]

        weekday_in: dict[int, list[float]] = defaultdict(list)
        weekday_out: dict[int, list[float]] = defaultdict(list)

        first_hist_date = min(t["date_obj"] for t in history)
        d_ptr = first_hist_date
        while d_ptr <= as_of_date:
            if not recent_active_months or _month_key(d_ptr) in recent_active_months:
                wd = d_ptr.weekday()
                weekday_in[wd].append(var_by_day_in.get(d_ptr, 0.0))
                weekday_out[wd].append(var_by_day_out.get(d_ptr, 0.0))
            d_ptr += timedelta(days=1)

        avg_wd_in = {
            wd: (statistics.mean(vals) if vals else 0.0) for wd, vals in weekday_in.items()
        }
        avg_wd_out = {
            wd: (statistics.mean(vals) if vals else 0.0) for wd, vals in weekday_out.items()
        }

        # Step 3: Future manual operations inside (as_of_date, as_of_date + horizon_days]
        end_forecast_date = as_of_date + timedelta(days=horizon_days)
        future_manual_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for t in self.transactions:
            if t.get("source") == "manual" and as_of_date < t["date_obj"] <= end_forecast_date:
                future_manual_by_date[t["date_obj"]].append(t)

        # Step 4: Roll forward day by day
        credit_cushion = (
            float(self.approved_credit["amount"])
            if self.approved_credit and self.approved_credit.get("amount", 0) > 0
            else 0.0
        )

        proj_balance = current_balance
        forecast_series: list[dict[str, Any]] = []
        upcoming_bills: list[dict[str, Any]] = []
        total_expected_in = 0.0
        total_expected_out = 0.0

        lowest_balance = current_balance
        lowest_date = as_of_date.isoformat()
        first_negative_date: str | None = (
            as_of_date.isoformat() if current_balance < 0 else None
        )
        last_negative_date: str | None = (
            as_of_date.isoformat() if current_balance < 0 else None
        )
        days_below_zero = 0

        for step in range(1, horizon_days + 1):
            f_date = as_of_date + timedelta(days=step)
            wd = f_date.weekday()
            day_in = avg_wd_in.get(wd, 0.0)
            day_out = avg_wd_out.get(wd, 0.0)
            day_events: list[dict[str, Any]] = []

            # Check scheduled recurring bills for this day
            for sched in recurring_schedules:
                if f_date.day != sched["typical_day"]:
                    continue
                months_diff = (f_date.year - sched["last_year"]) * 12 + (
                    f_date.month - sched["last_month"]
                )
                is_due = (
                    sched["cadence"] == "monthly"
                    or (sched["cadence"] == "quarterly" and months_diff % 3 == 0)
                )
                if is_due:
                    amt = sched["expected_amount"]
                    if amt >= 0:
                        day_in += amt
                    else:
                        day_out += amt
                    bill_item = {
                        "date": f_date.isoformat(),
                        "counterparty": sched["counterparty"],
                        "category": sched["category"],
                        "amount": amt,
                        "cadence": sched["cadence"],
                        "source": "detected_regular",
                    }
                    day_events.append(bill_item)
                    if amt < 0:
                        upcoming_bills.append(bill_item)

            # Check manual future operations on this date
            for m_tx in future_manual_by_date.get(f_date, []):
                amt = m_tx["amount"]
                if amt >= 0:
                    day_in += amt
                else:
                    day_out += amt
                m_item = {
                    "date": f_date.isoformat(),
                    "counterparty": m_tx["counterparty"],
                    "category": m_tx["category"],
                    "amount": amt,
                    "cadence": "one-off manual",
                    "source": "manual",
                }
                day_events.append(m_item)
                if amt < 0:
                    upcoming_bills.append(m_item)

            total_expected_in += day_in
            total_expected_out += abs(day_out)
            net_day = day_in + day_out
            proj_balance = round(proj_balance + net_day, 2)

            if proj_balance < lowest_balance:
                lowest_balance = proj_balance
                lowest_date = f_date.isoformat()

            if proj_balance < 0:
                days_below_zero += 1
                if first_negative_date is None:
                    first_negative_date = f_date.isoformat()
                last_negative_date = f_date.isoformat()

            forecast_series.append(
                {
                    "date": f_date.isoformat(),
                    "balance": proj_balance,
                    "balance_with_credit": round(proj_balance + credit_cushion, 2),
                    "expected_in": round(day_in, 2),
                    "expected_out": round(abs(day_out), 2),
                    "events": day_events,
                }
            )

        goes_below_zero = lowest_balance < 0
        shortfall_amount = round(abs(lowest_balance), 2) if goes_below_zero else 0.0
        # Recommended credit includes a 10% safety cushion rounded up to nearest 100
        recommended_credit = (
            float(math.ceil((shortfall_amount * 1.10) / 100.0) * 100)
            if goes_below_zero
            else 0.0
        )

        recovery_date: str | None = None
        if goes_below_zero and last_negative_date:
            last_neg_dt = date.fromisoformat(last_negative_date)
            if last_neg_dt < end_forecast_date:
                recovery_date = (last_neg_dt + timedelta(days=1)).isoformat()

        # Plain-language summaries (zero jargon)
        cur_sym = "€" if self.account_currency == "EUR" else f"{self.account_currency} "
        analysis_summary = (
            f"Starting from {as_of_date.isoformat()} with a balance of {cur_sym}{current_balance:,.2f}, "
            f"Greg checked your past payment patterns and found {len(recurring_schedules)} regular monthly/quarterly "
            f"payments. Over the next {horizon_days} days, expected income is "
            f"{cur_sym}{total_expected_in:,.2f} and expected expenses (including {len(upcoming_bills)} "
            f"scheduled bills) are {cur_sym}{total_expected_out:,.2f}."
        )

        if goes_below_zero:
            rec_text = (
                f" recovers back above zero on {recovery_date}"
                if recovery_date
                else f" stays below zero through the end of the {horizon_days}-day window"
            )
            recommendation_summary = (
                f"Warning: Your account balance is expected to drop below zero on {first_negative_date}, "
                f"reaching a lowest point of -{cur_sym}{shortfall_amount:,.2f} on {lowest_date} "
                f"({days_below_zero} days below zero; it{rec_text}). "
                f"To keep your account safely above zero without paying high unarranged bank overdraft fees, "
                f"you should request a short-term credit line of {cur_sym}{recommended_credit:,.2f} from your bank."
            )
        else:
            recommendation_summary = (
                f"Good news: Your account stays above zero for the entire next {horizon_days} days. "
                f"The lowest expected balance is {cur_sym}{lowest_balance:,.2f} on {lowest_date}. "
                f"You do not need any short-term bank credit right now."
            )

        return {
            "as_of_date": as_of_date.isoformat(),
            "horizon_days": horizon_days,
            "current_balance": current_balance,
            "total_expected_in": round(total_expected_in, 2),
            "total_expected_out": round(total_expected_out, 2),
            "detected_regular_schedules_count": len(recurring_schedules),
            "upcoming_bills": upcoming_bills,
            "lowest_predicted_balance": lowest_balance,
            "lowest_predicted_date": lowest_date,
            "goes_below_zero": goes_below_zero,
            "first_negative_date": first_negative_date,
            "recovery_date": recovery_date,
            "days_below_zero": days_below_zero,
            "shortfall_amount": shortfall_amount,
            "recommended_credit_amount": recommended_credit,
            "analysis_summary": analysis_summary,
            "recommendation_summary": recommendation_summary,
            "forecast_series": forecast_series,
        }


STORE = LedgerStore()
