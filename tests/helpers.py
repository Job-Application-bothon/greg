"""Shared synthetic ledger and HTTP mock helpers for Greg tests."""
from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

from greg.domain.ledger import LedgerStore


class FakeHTTPResponse(io.BytesIO):
    """Context-manager BytesIO mock mimicking `urllib.request.urlopen` responses."""

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def synthetic_ledger(
    start: date,
    end: date,
    *,
    growth: float = 1.05,
    scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Generate a purely synthetic SME bank ledger between `start` and `end`."""
    txs: list[dict[str, Any]] = []
    d = start
    base_year = start.year
    idx = 0
    while d <= end:
        idx += 1
        season = 1.0 + 0.4 * (d.month in (6, 7, 8)) + 0.2 * (d.month == 12)
        yr_factor = growth ** max(0, d.year - base_year)
        amt_in = round(1000.0 * scale * season * yr_factor, 2)
        txs.append(
            {
                "id": f"syn-{idx}-in",
                "date": d.isoformat(),
                "date_obj": d,
                "amount": amt_in,
                "original_amount": amt_in,
                "original_currency": "EUR",
                "fx_rate": None,
                "category": "Other",
                "counterparty": "Customers",
                "raw_type": "CARD",
                "description": "Daily sales",
                "source": "csv",
            }
        )
        amt_out = round(-350.0 * scale * season * yr_factor, 2)
        txs.append(
            {
                "id": f"syn-{idx}-out",
                "date": d.isoformat(),
                "date_obj": d,
                "amount": amt_out,
                "original_amount": amt_out,
                "original_currency": "EUR",
                "fx_rate": None,
                "category": "Other",
                "counterparty": "Supplier",
                "raw_type": "CARD",
                "description": "Supplies",
                "source": "csv",
            }
        )
        if d.day == 1:
            amt_rent = round(-5500.0 * scale, 2)
            txs.append(
                {
                    "id": f"syn-{idx}-rent",
                    "date": d.isoformat(),
                    "date_obj": d,
                    "amount": amt_rent,
                    "original_amount": amt_rent,
                    "original_currency": "EUR",
                    "fx_rate": None,
                    "category": "Other",
                    "counterparty": "Landlord",
                    "raw_type": "RENT",
                    "description": "Monthly rent",
                    "source": "csv",
                }
            )
        if d.day == 28:
            amt_sal = round(-8500.0 * scale, 2)
            txs.append(
                {
                    "id": f"syn-{idx}-sal",
                    "date": d.isoformat(),
                    "date_obj": d,
                    "amount": amt_sal,
                    "original_amount": amt_sal,
                    "original_currency": "EUR",
                    "fx_rate": None,
                    "category": "Salaries",
                    "counterparty": "Payroll",
                    "raw_type": "SALARY",
                    "description": "Monthly salaries",
                    "source": "csv",
                }
            )
        d += timedelta(days=1)
    return txs


def make_store_from_txs(
    txs: list[dict[str, Any]],
    opening_balance: float = 10000.0,
) -> LedgerStore:
    """Create a populated `LedgerStore` directly from synthetic transactions."""
    store = LedgerStore()
    store.transactions = list(txs)
    store.opening_balance = opening_balance
    store.filename = "synthetic.csv"
    store.account_currency = "EUR"
    return store


def build_synthetic_csv(start_dt: date, end_dt: date) -> str:
    """Build a semicolon-separated synthetic daily CSV between `start_dt` and `end_dt`."""
    lines = ["Booking Date;Transaction Type;Beneficiary/Payer;Payment Reference;Amount;Currency"]
    d = start_dt
    while d <= end_dt:
        season = 1.0 + 0.35 * (d.month in (6, 7, 8))
        lines.append(
            f"{d.strftime('%Y-%m-%d')};CARD SETTLEMENT;Customers;Sales;{round(900.0 * season, 2)};EUR"
        )
        lines.append(
            f"{d.strftime('%Y-%m-%d')};SUPPLIER;Wholesaler;Goods;{round(-320.0 * season, 2)};EUR"
        )
        if d.day == 1:
            lines.append(f"{d.strftime('%Y-%m-%d')};RENT PAYMENT;Landlord;Rent;-4000.00;EUR")
        if d.day == 28:
            lines.append(f"{d.strftime('%Y-%m-%d')};SALARY;Staff;Wages;-6000.00;EUR")
        d += timedelta(days=1)
    return "\n".join(lines)
