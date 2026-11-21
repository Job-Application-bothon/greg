"""In-memory transaction ledger state and UI view projection."""
from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, timedelta
from typing import Any

from greg.domain.categories import VALID_CATEGORIES, normalize_category
from greg.domain.csv_import import parse_csv_transactions, parse_date
from greg.forecasting.orchestrator import run_forecast

_MONTH_NAMES = (
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
)


def month_key(d: date) -> str:
    """Format a date into `YYYY-MM`."""
    return f"{d.year:04d}-{d.month:02d}"


def month_label(ym: str) -> str:
    """Format `YYYY-MM` as a human-readable month label such as `January 2025`."""
    year, month = (int(x) for x in ym.split("-"))
    return f"{_MONTH_NAMES[month]} {year}"


class LedgerStore:
    """In-memory zero-hardcoding store for imported CSVs and manual operations."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Clear all loaded transactions, balances, credit state, and planner cache."""
        self.transactions: list[dict[str, Any]] = []
        self.opening_balance: float = 0.0
        self.filename: str | None = None
        self.account_currency: str = "EUR"
        self.approved_credit: dict[str, Any] | None = None
        self._planner_cache: dict[tuple[Any, ...], dict[str, Any]] = {}

    def import_csv(
        self,
        csv_text: str,
        filename: str = "uploaded.csv",
        opening_balance: float = 0.0,
        keep_manual: bool = False,
        llm_classifier: Callable[[list[dict[str, str]]], dict[str, str]] | None = None,
        column_map: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Parse and replace CSV transactions in this ledger store."""
        parsed, dominant_currency = parse_csv_transactions(
            csv_text=csv_text,
            column_map=column_map,
            llm_classifier=llm_classifier,
        )
        manual_existing = (
            [t for t in self.transactions if t.get("source") == "manual"] if keep_manual else []
        )

        self.transactions = parsed + manual_existing
        self.transactions.sort(key=lambda t: (t["date"], t["id"]))
        self.opening_balance = float(opening_balance)
        self.filename = filename
        self.approved_credit = None
        self.account_currency = dominant_currency
        self._planner_cache.clear()

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
        """Append a manual income or expense entry to the ledger."""
        dt = parse_date(date_str)
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
        self._planner_cache.clear()
        return self._serialize_tx(op)

    def delete_operation(self, op_id: str) -> bool:
        """Delete a manual transaction by id; imported CSV rows cannot be deleted individually."""
        before = len(self.transactions)
        self.transactions = [
            t for t in self.transactions if not (t["id"] == op_id and t.get("source") == "manual")
        ]
        if len(self.transactions) < before:
            self._planner_cache.clear()
        return len(self.transactions) < before

    def set_opening_balance(self, amount: float) -> None:
        """Update the opening balance used for daily running balance calculations."""
        self.opening_balance = round(float(amount), 2)

    def approve_credit(self, amount: float, month: str, horizon_days: int) -> dict[str, Any]:
        """Record a simulated bank credit line cushion on the ledger."""
        from datetime import datetime

        val = round(max(0.0, float(amount)), 2)
        if val <= 0:
            self.approved_credit = None
        else:
            self.approved_credit = {
                "amount": val,
                "month": month,
                "horizon_days": int(horizon_days),
                "approved_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
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

    def get_view(
        self,
        selected_month: str | None = None,
        horizon_days: int = 30,
        horizon_months: int | None = None,
    ) -> dict[str, Any]:
        """Compute complete frontend & LLM summary payload for the requested month and horizon."""
        horizon_days = max(7, min(180, int(horizon_days)))
        if horizon_months is not None:
            horizon_months = max(1, min(12, int(horizon_months)))
        if not self.transactions:
            return {
                "has_data": False,
                "filename": None,
                "opening_balance": self.opening_balance,
                "currency": self.account_currency,
                "months": [],
                "selected_month": None,
                "monthly_history_series": [],
            }

        months_set = sorted({month_key(t["date_obj"]) for t in self.transactions})
        if not selected_month or selected_month not in months_set:
            selected_month = months_set[-1]

        all_dates = [t["date_obj"] for t in self.transactions]
        min_date = min(all_dates)
        max_date = max(all_dates)

        tx_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        tx_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
        tx_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for t in self.transactions:
            tx_by_date[t["date_obj"]].append(t)
            tx_by_month[month_key(t["date_obj"])].append(t)
            tx_by_year[t["date_obj"].year].append(t)

        sel_year, sel_m = (int(x) for x in selected_month.split("-"))
        first_of_month = date(sel_year, sel_m, 1)
        next_month_first = date(sel_year + 1, 1, 1) if sel_m == 12 else date(sel_year, sel_m + 1, 1)
        last_of_month = next_month_first - timedelta(days=1)

        end_walk = max(last_of_month, max_date)
        running_bal = self.opening_balance
        daily_balances: dict[date, float] = {}
        cur = min_date
        while cur <= end_walk:
            day_sum = sum(t["amount"] for t in tx_by_date.get(cur, []))
            running_bal = round(running_bal + day_sum, 2)
            daily_balances[cur] = running_bal
            cur += timedelta(days=1)

        prev_day = first_of_month - timedelta(days=1)
        month_start_balance = (
            daily_balances[prev_day] if prev_day >= min_date else round(self.opening_balance, 2)
        )
        month_end_balance = daily_balances.get(last_of_month, month_start_balance)

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

        income_breakdown = self._build_breakdown(
            [t for t in month_txs if t["amount"] > 0], is_expense=False
        )
        expense_breakdown = self._build_breakdown(
            [t for t in month_txs if t["amount"] < 0], is_expense=True
        )

        yearly_summaries: list[dict[str, Any]] = []
        for yr in sorted(tx_by_year):
            y_txs = tx_by_year[yr]
            y_inc = round(sum(t["amount"] for t in y_txs if t["amount"] > 0), 2)
            y_exp = round(sum(abs(t["amount"]) for t in y_txs if t["amount"] < 0), 2)
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
                    "profit": round(y_inc - y_exp, 2),
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
                    "label": month_label(mk),
                    "total_income": m_inc,
                    "total_expenses": m_exp,
                    "profit": round(m_inc - m_exp, 2),
                    "end_balance": daily_balances.get(m_last, round(self.opening_balance, 2)),
                }
            )

        history_series: list[dict[str, Any]] = []
        d_ptr = min_date
        while d_ptr <= last_of_month:
            history_series.append(
                {
                    "date": d_ptr.isoformat(),
                    "balance": daily_balances.get(d_ptr, self.opening_balance),
                }
            )
            d_ptr += timedelta(days=1)

        month_end_balance_map: dict[str, float] = {}
        monthly_history_all: list[dict[str, Any]] = []
        ym_y, ym_m = min_date.year, min_date.month
        while (ym_y, ym_m) <= (last_of_month.year, last_of_month.month):
            mk = f"{ym_y:04d}-{ym_m:02d}"
            m_next = date(ym_y + 1, 1, 1) if ym_m == 12 else date(ym_y, ym_m + 1, 1)
            m_last = m_next - timedelta(days=1)
            m_bal = daily_balances.get(m_last, round(self.opening_balance, 2))
            month_end_balance_map[mk] = m_bal
            monthly_history_all.append({"date": mk, "balance": m_bal})
            ym_y, ym_m = (ym_y + 1, 1) if ym_m == 12 else (ym_y, ym_m + 1)

        forecast = run_forecast(
            transactions=self.transactions,
            as_of_date=last_of_month,
            current_balance=month_end_balance,
            horizon_days=horizon_days,
            horizon_months=horizon_months,
            month_end_balance_map=month_end_balance_map,
            account_currency=self.account_currency,
            approved_credit=self.approved_credit,
            planner_cache=self._planner_cache,
        )
        if forecast.get("engine") == "greg_planner" and forecast.get("last_history_month"):
            last_hm = forecast["last_history_month"]
            monthly_history_series = [m for m in monthly_history_all if m["date"] <= last_hm]
        else:
            monthly_history_series = monthly_history_all

        return {
            "has_data": True,
            "filename": self.filename,
            "opening_balance": self.opening_balance,
            "currency": self.account_currency,
            "total_transactions": len(self.transactions),
            "months": [{"key": m, "label": month_label(m)} for m in months_set],
            "selected_month": selected_month,
            "selected_month_label": month_label(selected_month),
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
            "monthly_history_series": monthly_history_series,
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
