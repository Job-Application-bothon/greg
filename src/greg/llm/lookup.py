"""Find the dates and months a chat question mentions and add their exact ledger data to the prompt."""
from __future__ import annotations

import bisect
import re
from collections import defaultdict
from datetime import date
from typing import Any

from greg.domain.ledger import LedgerStore, month_label
from greg.llm.prompts import _clean, _currency_prefix

MAX_DAYS = 4
MAX_MONTHS = 3
MAX_TX_PER_DAY = 12

_MONTH_NAMES: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
    # German, for German bank customers.
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7,
    "oktober": 10, "okt": 10, "dezember": 12, "dez": 12,
}
# Bare month words that are too ambiguous on their own ("may I ask", "mar", ...).
_BARE_EXCLUDED = {"may", "mai", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
                  "oct", "nov", "dec", "okt", "dez"}

_MN = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th|\.)?"
_RE_ISO_DAY = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_ISO_MONTH = re.compile(r"\b(\d{4})-(\d{1,2})\b(?!-\d)")
_RE_NUM_DAY = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})\b")
_RE_NUM_MONTH = re.compile(r"\b(\d{1,2})[./](\d{4})\b")
_RE_DAY_NAME = re.compile(
    rf"\b(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_MN})\b\.?(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
_RE_NAME_DAY = re.compile(
    rf"\b({_MN})\b\.?\s+(\d{{1,2}}){_ORD}(?!\d)(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
_RE_NAME_YEAR = re.compile(rf"\b({_MN})\b\.?\s+(\d{{4}})\b", re.IGNORECASE)
_RE_NAME = re.compile(rf"\b({_MN})\b", re.IGNORECASE)
# Ambiguous month words count only after a preposition: "in May", "for Mar".
_RE_PREP_NAME = re.compile(
    rf"\b(?:in|of|for|during|since|until|from|through|to|im|seit|bis)\s+({_MN})\b(?!\s+(?:i|we)\b)",
    re.IGNORECASE,
)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def extract_dates(text: str, data_years: list[int]) -> tuple[list[date], list[str]]:
    """Return (days, months as YYYY-MM) mentioned in text. Missing years use the data's years."""
    days: list[date] = []
    months: list[str] = []
    taken: list[tuple[int, int]] = []

    def free(m: re.Match[str]) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    def add_day(y: int | None, mo: int, d: int, m: re.Match[str]) -> None:
        taken.append(m.span())
        for year in [y] if y else data_years:
            dt = _safe_date(year, mo, d)
            if dt and dt not in days:
                days.append(dt)

    def add_month(y: int | None, mo: int, m: re.Match[str]) -> None:
        taken.append(m.span())
        if not 1 <= mo <= 12:
            return
        for year in [y] if y else data_years:
            key = f"{year:04d}-{mo:02d}"
            if key not in months:
                months.append(key)

    for m in _RE_ISO_DAY.finditer(text):
        add_day(int(m[1]), int(m[2]), int(m[3]), m)
    for m in _RE_NUM_DAY.finditer(text):
        if not free(m):
            continue
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y = y + 2000 if y < 100 else y
        day, mo = (b, a) if a <= 12 < b else (a, b)  # day-first unless clearly month-first
        add_day(y, mo, day, m)
    for m in _RE_DAY_NAME.finditer(text):
        if free(m):
            add_day(int(m[3]) if m[3] else None, _MONTH_NAMES[m[2].lower()], int(m[1]), m)
    for m in _RE_NAME_DAY.finditer(text):
        if free(m):
            add_day(int(m[3]) if m[3] else None, _MONTH_NAMES[m[1].lower()], int(m[2]), m)
    for m in _RE_ISO_MONTH.finditer(text):
        if free(m):
            add_month(int(m[1]), int(m[2]), m)
    for m in _RE_NUM_MONTH.finditer(text):
        if free(m):
            add_month(int(m[2]), int(m[1]), m)
    for m in _RE_NAME_YEAR.finditer(text):
        if free(m):
            add_month(int(m[2]), _MONTH_NAMES[m[1].lower()], m)
    for m in _RE_PREP_NAME.finditer(text):
        if free(m):
            add_month(None, _MONTH_NAMES[m[1].lower()], m)
    for m in _RE_NAME.finditer(text):
        if free(m) and m[1].lower() not in _BARE_EXCLUDED:
            add_month(None, _MONTH_NAMES[m[1].lower()], m)

    day_months = {d.strftime("%Y-%m") for d in days}
    months = [mk for mk in months if mk not in day_months]
    return days[:MAX_DAYS], months[:MAX_MONTHS]


class _Balances:
    """End-of-day balance lookup over the store's transactions."""

    def __init__(self, store: LedgerStore) -> None:
        txs = sorted(store.transactions, key=lambda t: t["date_obj"])
        self.dates = [t["date_obj"] for t in txs]
        self.cumulative: list[float] = []
        running = float(store.opening_balance)
        for t in txs:
            running += float(t["amount"])
            self.cumulative.append(running)
        self.opening = float(store.opening_balance)

    def end_of(self, d: date) -> float:
        idx = bisect.bisect_right(self.dates, d)
        return round(self.cumulative[idx - 1] if idx else self.opening, 2)


def _tx_line(t: dict[str, Any], sym: str) -> str:
    desc = _clean(t.get("description") or "")
    return (
        f"    - {'+' if t['amount'] >= 0 else '-'}{sym}{abs(t['amount']):,.2f} "
        f"{_clean(t['counterparty'])} ({t['category']})"
        + (f": {desc}" if desc else "")
    )


def build_date_lookup(store: LedgerStore, text: str) -> str:
    """Build a DATE LOOKUP prompt section for the dates/months in text, or "" if none."""
    if not store.transactions:
        return ""
    first = min(t["date_obj"] for t in store.transactions)
    last = max(t["date_obj"] for t in store.transactions)
    years = list(range(first.year, last.year + 1))
    days, months = extract_dates(text, years)
    if not days and not months:
        return ""

    sym = _currency_prefix(store.account_currency)
    balances = _Balances(store)
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in store.transactions:
        by_day[t["date_obj"]].append(t)
        by_month[t["date_obj"].strftime("%Y-%m")].append(t)

    lines = [
        "DATE LOOKUP (exact bank data for the dates in the user's question; prefer it over "
        "your own guesses):"
    ]
    for d in sorted(days):
        label = f"{d.isoformat()} ({d.strftime('%A')}, {d.day} {d.strftime('%B %Y')})"
        if not first <= d <= last:
            lines.append(
                f"- {label}: no bank data for this date (data covers {first} to {last})."
            )
            continue
        txs = by_day.get(d, [])
        start = balances.end_of(date.fromordinal(d.toordinal() - 1))
        end = balances.end_of(d)
        if not txs:
            lines.append(f"- {label}: no payments on this day. Balance: {sym}{end:,.2f}.")
            continue
        income = sum(t["amount"] for t in txs if t["amount"] > 0)
        expenses = sum(-t["amount"] for t in txs if t["amount"] < 0)
        lines.append(
            f"- {label}: {len(txs)} payments. Money in {sym}{income:,.2f}, money out "
            f"{sym}{expenses:,.2f}. Balance at start of day {sym}{start:,.2f}, "
            f"at end of day {sym}{end:,.2f}."
        )
        ranked = sorted(txs, key=lambda t: -abs(t["amount"]))
        lines.extend(_tx_line(t, sym) for t in ranked[:MAX_TX_PER_DAY])
        if len(ranked) > MAX_TX_PER_DAY:
            lines.append(f"    - ...and {len(ranked) - MAX_TX_PER_DAY} smaller payments")

    for mk in sorted(months):
        txs = by_month.get(mk, [])
        if not txs:
            lines.append(
                f"- {month_label(mk)}: no bank data for this month (data covers {first} to {last})."
            )
            continue
        income = sum(t["amount"] for t in txs if t["amount"] > 0)
        expenses = sum(-t["amount"] for t in txs if t["amount"] < 0)
        by_cat: dict[str, float] = defaultdict(float)
        out_by_who: dict[str, float] = defaultdict(float)
        in_by_who: dict[str, float] = defaultdict(float)
        for t in txs:
            if t["amount"] < 0:
                by_cat[t["category"]] += -t["amount"]
                out_by_who[_clean(t["counterparty"])] += -t["amount"]
            else:
                in_by_who[_clean(t["counterparty"])] += t["amount"]
        month_days = sorted({t["date_obj"] for t in txs})
        low_day = min(month_days, key=balances.end_of)
        cats = ", ".join(f"{c}: {sym}{v:,.2f}" for c, v in sorted(by_cat.items()))
        top_out = ", ".join(
            f"{who}: {sym}{v:,.2f}" for who, v in sorted(out_by_who.items(), key=lambda x: -x[1])[:6]
        )
        top_in = ", ".join(
            f"{who}: {sym}{v:,.2f}" for who, v in sorted(in_by_who.items(), key=lambda x: -x[1])[:3]
        )
        lines.append(
            f"- {month_label(mk)} ({mk}): {len(txs)} payments. Income {sym}{income:,.2f}, "
            f"expenses {sym}{expenses:,.2f}. Expenses by category: {cats or 'none'}. "
            f"Biggest payments out: {top_out or 'none'}. Biggest money in: {top_in or 'none'}. "
            f"Lowest end-of-day balance {sym}{balances.end_of(low_day):,.2f} on {low_day}."
        )
    return "\n".join(lines)
