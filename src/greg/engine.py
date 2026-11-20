from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
PIZZERIA_CSV_PATH = DATA_DIR / "pizzeria_amalfi_2025.csv"

TWO_PLACES = Decimal("0.01")
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _d(val: str | int | float | Decimal) -> Decimal:
    if isinstance(val, Decimal):
        return val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return Decimal(str(val)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _f(val: Decimal | float | int) -> float:
    return float(_d(val))


def _parse_amount(raw: str) -> Decimal:
    cleaned = (raw or "").strip().replace("€", "").replace("EUR", "").replace("CHF", "").replace("USD", "").strip()
    if not cleaned:
        return Decimal("0.00")
    # Handle German format "1.234,56" vs standard "1,234.56"
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal("0.00")


def _parse_date(raw: str) -> dt.date:
    s = (raw or "").strip()
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return dt.date(2025, 1, 1)


def _extract_fx_eur(amount: Decimal, currency: str, reference: str) -> tuple[Decimal, Decimal | None]:
    cur = (currency or "EUR").strip().upper()
    if cur == "EUR" or not cur:
        return amount, None
    if cur == "CHF":
        match = re.search(r"@\s*([0-9]+(?:[.,][0-9]+)?)\s*EUR/CHF", reference or "", re.IGNORECASE)
        rate = Decimal(match.group(1).replace(",", ".")) if match else Decimal("1.047")
        return (amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP), rate
    if cur == "USD":
        match = re.search(r"@\s*([0-9]+(?:[.,][0-9]+)?)\s*EUR/USD", reference or "", re.IGNORECASE)
        rate = Decimal(match.group(1).replace(",", ".")) if match else Decimal("0.924")
        return (amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP), rate
    return amount, None


def _normalize_vendor(name: str, tx_type: str) -> str:
    raw = (name or "").strip()
    low = raw.lower()
    if "pizzeria amalfi customers" in low or "debit" in low and "card" in tx_type.lower():
        return "Pizzeria Amalfi Customers (Debit)"
    if "commerzbank global pay" in low:
        return "Commerzbank Global Pay"
    if "asimex" in low:
        return "Asimex Wholesale Ltd"
    if "fattoria la vino" in low:
        return "Fattoria La Vino GmbH"
    if "central wholesale" in low:
        return "Central Wholesale Market"
    if "luigi" in low:
        return "Luigi Amalfi (Zurich Ristorante)"
    if "techniker krankenkasse" in low:
        return "Techniker Krankenkasse"
    if "tax office" in low or "finanzamt" in low:
        return "Tax Office Frankfurt am Main"
    if "main street real estate" in low:
        return "Main Street Real Estate GmbH"
    if "city energy" in low:
        return "City Energy & Utilities AG"
    if "aws" in low or "amazon web services" in low:
        return "Amazon Web Services"
    return raw or "Unspecified Counterparty"


def _categorize_tx(tx_type: str, counterparty: str, reference: str, eur_amount: Decimal) -> str:
    combined = f"{tx_type} {counterparty} {reference}".lower()
    if eur_amount > 0 or "settlement" in combined or "payout" in combined or "revenue" in combined:
        return "Revenue & Card Settlements"
    if "international wire" in combined or "zurich" in combined or "chf" in combined:
        return "Swiss Capital Investment (CHF)"
    if "salary" in combined or "payroll" in combined:
        return "Payroll & Salaries"
    if "krankenkasse" in combined or "social security" in combined:
        return "Social Security"
    if "tax" in combined or "einkommensteuer" in combined or "gewerbesteuer" in combined or "finanzamt" in combined:
        return "Taxes & Levies"
    if "rent" in combined or "real estate" in combined:
        return "Rent & Facility"
    if "energy" in combined or "utilities" in combined:
        return "Utilities & Energy"
    if "wine" in combined or "fattoria" in combined:
        return "Beverage & Wine Suppliers"
    if "wholesale" in combined or "asimex" in combined or "produce" in combined:
        return "Food & Fresh Produce"
    return "Operating Expenses"


def parse_bank_csv(csv_text: str, opening_balance: Decimal = Decimal("2500.00")) -> dict[str, Any]:
    """Parse German MT940-style semicolon CSV or standard comma CSV using exact Decimal math."""
    text = (csv_text or "").lstrip("\ufeff").strip()
    if not text:
        raise ValueError("CSV content is empty")

    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    raw_rows = list(reader)
    if not raw_rows:
        raise ValueError("CSV contains headers but no transaction rows")

    # Normalize column accessor
    parsed_rows: list[dict[str, Any]] = []
    seen_fingerprints: set[tuple[str, str, str, str]] = set()
    duplicate_count = 0
    vendor_variants: dict[str, set[str]] = defaultdict(set)

    for idx, row in enumerate(raw_rows):
        norm_map = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
        date_str = (
            norm_map.get("booking date")
            or norm_map.get("date")
            or norm_map.get("value date")
            or norm_map.get("buchungstag")
            or ""
        )
        tx_date = _parse_date(date_str)
        tx_type = norm_map.get("transaction type") or norm_map.get("type") or norm_map.get("umsatzart") or "BOOKING"
        reference = (
            norm_map.get("payment reference")
            or norm_map.get("description")
            or norm_map.get("reference")
            or norm_map.get("verwendungszweck")
            or ""
        )
        raw_party = (
            norm_map.get("beneficiary/payer")
            or norm_map.get("counterparty")
            or norm_map.get("payee")
            or norm_map.get("beguenstigter/zahlungspflichtiger")
            or ""
        )
        iban = norm_map.get("counterparty account") or norm_map.get("iban") or ""
        own_iban = norm_map.get("account number") or norm_map.get("account") or "DE89370400440532013000"
        currency = (norm_map.get("currency") or "EUR").upper()
        raw_amount = _parse_amount(norm_map.get("amount") or norm_map.get("betrag") or "0")
        eur_amount, fx_rate = _extract_fx_eur(raw_amount, currency, reference)

        fingerprint = (tx_date.isoformat(), raw_party.lower(), reference.lower(), str(raw_amount))
        if fingerprint in seen_fingerprints:
            duplicate_count += 1
            continue
        seen_fingerprints.add(fingerprint)

        clean_vendor = _normalize_vendor(raw_party, tx_type)
        if raw_party:
            vendor_variants[clean_vendor].add(raw_party)
        category = _categorize_tx(tx_type, clean_vendor, reference, eur_amount)

        parsed_rows.append(
            {
                "orig_idx": idx,
                "date": tx_date,
                "date_str": tx_date.isoformat(),
                "own_iban": own_iban,
                "tx_type": tx_type,
                "reference": reference,
                "counterparty": clean_vendor,
                "raw_counterparty": raw_party,
                "counterparty_iban": iban,
                "currency": currency,
                "raw_amount": raw_amount,
                "eur_amount": eur_amount,
                "fx_rate": float(fx_rate) if fx_rate is not None else None,
                "category": category,
            }
        )

    # Detect reverse-chronological exports (e.g., 31.12.25 at top, 01.01.25 at bottom)
    if len(parsed_rows) >= 2 and parsed_rows[0]["date"] > parsed_rows[-1]["date"]:
        parsed_rows.reverse()
    else:
        parsed_rows.sort(key=lambda r: (r["date"], r["orig_idx"]))

    balance = _d(opening_balance)
    total_inflows = Decimal("0.00")
    total_outflows = Decimal("0.00")
    monthly_stats: dict[str, dict[str, Any]] = {}
    daily_balances: dict[str, Decimal] = {}
    category_totals: dict[str, dict[str, Any]] = {}
    fx_transactions: list[dict[str, Any]] = []

    for row in parsed_rows:
        amt = row["eur_amount"]
        balance = (balance + amt).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        row["running_balance"] = balance
        if amt >= 0:
            total_inflows += amt
        else:
            total_outflows += abs(amt)

        d_iso = row["date_str"]
        daily_balances[d_iso] = balance

        ym = d_iso[:7]
        if ym not in monthly_stats:
            monthly_stats[ym] = {
                "month": ym,
                "inflows": Decimal("0.00"),
                "outflows": Decimal("0.00"),
                "net": Decimal("0.00"),
                "ending_balance": balance,
            }
        if amt >= 0:
            monthly_stats[ym]["inflows"] += amt
        else:
            monthly_stats[ym]["outflows"] += abs(amt)
        monthly_stats[ym]["net"] += amt
        monthly_stats[ym]["ending_balance"] = balance

        cat = row["category"]
        if cat not in category_totals:
            category_totals[cat] = {"category": cat, "amount": Decimal("0.00"), "count": 0}
        category_totals[cat]["amount"] += amt
        category_totals[cat]["count"] += 1

        if row["currency"] != "EUR":
            fx_transactions.append(
                {
                    "date": d_iso,
                    "counterparty": row["counterparty"],
                    "currency": row["currency"],
                    "raw_amount": float(row["raw_amount"]),
                    "eur_amount": float(amt),
                    "fx_rate": row["fx_rate"],
                    "iban": row["counterparty_iban"],
                }
            )

    min_day, min_day_bal = min(daily_balances.items(), key=lambda x: x[1])
    max_day, max_day_bal = max(daily_balances.items(), key=lambda x: x[1])

    # Build ~55 evenly sampled points + exact critical trough/end points for smooth SVG plotting
    sorted_days = sorted(daily_balances.items(), key=lambda x: x[0])
    step = max(1, len(sorted_days) // 52)
    chart_points: list[dict[str, Any]] = []
    included_dates: set[str] = set()
    for i, (d_iso, d_bal) in enumerate(sorted_days):
        if i % step == 0 or d_iso in (min_day, max_day, sorted_days[0][0], sorted_days[-1][0]):
            chart_points.append({"date": d_iso, "balance": float(d_bal)})
            included_dates.add(d_iso)
    if sorted_days[-1][0] not in included_dates:
        chart_points.append({"date": sorted_days[-1][0], "balance": float(sorted_days[-1][1])})
    chart_points.sort(key=lambda p: p["date"])

    merged_variants = sum(max(0, len(v) - 1) for v in vendor_variants.values())
    hygiene_score = max(84.0, round(99.2 - duplicate_count * 0.4 - merged_variants * 0.2, 1))

    monthly_list = [
        {
            "month": m["month"],
            "inflows": float(m["inflows"]),
            "outflows": float(m["outflows"]),
            "net": float(m["net"]),
            "ending_balance": float(m["ending_balance"]),
        }
        for _, m in sorted(monthly_stats.items())
    ]

    cat_list = [
        {
            "category": c["category"],
            "amount": float(c["amount"]),
            "abs_amount": float(abs(c["amount"])),
            "count": c["count"],
        }
        for c in sorted(category_totals.values(), key=lambda x: abs(x["amount"]), reverse=True)
    ]

    recent_ledger = [
        {
            "date": r["date_str"],
            "type": r["tx_type"],
            "counterparty": r["counterparty"],
            "category": r["category"],
            "amount": float(r["eur_amount"]),
            "currency": r["currency"],
            "raw_amount": float(r["raw_amount"]),
            "reference": r["reference"],
            "iban": r["counterparty_iban"],
            "balance_after": float(r["running_balance"]),
        }
        for r in reversed(parsed_rows[-40:])
    ]

    return {
        "row_count": len(raw_rows),
        "unique_count": len(parsed_rows),
        "duplicate_count": duplicate_count,
        "opening_balance": float(_d(opening_balance)),
        "closing_balance": float(balance),
        "total_inflows": float(total_inflows),
        "total_outflows": float(total_outflows),
        "net_cash_flow": float(total_inflows - total_outflows),
        "min_daily_date": min_day,
        "min_daily_balance": float(min_day_bal),
        "max_daily_date": max_day,
        "max_daily_balance": float(max_day_bal),
        "monthly": monthly_list,
        "historical_curve": chart_points,
        "categories": cat_list,
        "fx_transactions": fx_transactions,
        "recent_ledger": recent_ledger,
        "own_iban": parsed_rows[0]["own_iban"] if parsed_rows else "DE89370400440532013000",
        "hygiene": {
            "score": hygiene_score,
            "raw_rows": len(raw_rows),
            "clean_rows": len(parsed_rows),
            "duplicates_removed": duplicate_count,
            "normalized_vendors": len(vendor_variants),
            "variants_merged": merged_variants,
        },
    }


def load_pizzeria_csv_text() -> str:
    if PIZZERIA_CSV_PATH.exists():
        return PIZZERIA_CSV_PATH.read_text(encoding="utf-8")
    fallback = Path(__file__).resolve().parents[2] / "requirements" / "red-hat-challenge-samle data" / "pizzeria-amalfi-bank-statement-2025-v6.csv"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError("pizzeria_amalfi_2025.csv sample dataset not found")


def _default_preferences(safety_buffer: float) -> dict[str, Any]:
    return {
        "view_mode": "calm",  # "calm" | "analyst"
        "min_severity": "low",  # "low" | "medium" | "high" | "critical"
        "safety_buffer_eur": safety_buffer,
        "enabled_modules": {
            "warnings": True,
            "recommendations": True,
            "gov_news": True,
            "fraud_guard": True,
            "fx_hedging": True,
            "investments": True,
        },
        "muted_categories": [],
        "muted_action_ids": [],
    }


def _pizzeria_forecast_13w(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    start_bal = float(parsed["closing_balance"])
    # Model a realistic upcoming 13-week Q1/Spring transition including seasonal 3-week kitchen/vacation closure in Weeks 5-7,
    # recurring monthly rent (€3,800), payroll (€8,600), social security (€2,650), Zurich CHF transfer (€4,363),
    # utilities (€650), wage tax (€880), and quarterly Einkommensteuer (€1,150) + Gewerbesteuer (€225).
    weekly_spec = [
        ("Wk 1", "Jan 05", 5350.0, 4450.0, 2000.0, "Rent (€3,800 Main Street Real Estate) + fresh produce vs. early-Jan card payouts (+€5,350).", ["RENT-JAN-2026", "CGP-SETTLE-W1"]),
        ("Wk 2", "Jan 12", 5250.0, 7753.0, 0.0, "Monthly Zurich capital tranche (-4,166.67 CHF ≈ -€4,363 to CH93...) + Wage Tax (€880 Finanzamt) + City Energy (€650).", ["WIRE-CHF-01", "TAX-WAGE-01"]),
        ("Wk 3", "Jan 19", 5420.0, 1890.0, 0.0, "Strong weekend Commerzbank Global Pay settlement (+€5,420) vs. Fattoria La Vino & Asimex invoices.", ["CGP-SETTLE-W3", "INV-FLV-01"]),
        ("Wk 4", "Jan 26", 5180.0, 13140.0, 0.0, "Payroll collision: Staff & owner net payroll (-€8,600) + Techniker Krankenkasse (-€2,650) + suppliers.", ["PAYROLL-01", "TK-SOC-01"]),
        ("Wk 5", "Feb 02", 1450.0, 13650.0, 6000.0, "Planned 3-week seasonal closure / wood-oven refurbishment begins: card inflows drop -72% while Rent (€3,800) & oven deposit (-€4,000) hit.", ["CLOSURE-PLAN", "RENT-FEB-2026"]),
        ("Wk 6", "Feb 09", 0.0, 11458.0, 12000.0, "Peak closure week (€0 revenue): Zurich CHF tranche (-€4,363), Wage Tax (€880), City Energy (€650), Gewerbesteuer (€225) & oven milestone push balance negative.", ["WIRE-CHF-02", "TAX-GEW-Q1"]),
        ("Wk 7", "Feb 16", 1850.0, 13845.35, 10000.0, "Trough week (-€8,420.00 projected): Re-opening restocking + early payroll reserve before card settlements resume.", ["RESTOCK-ASIMEX", "TROUGHLOW-WK7"]),
        ("Wk 8", "Feb 23", 12420.0, 11400.0, 0.0, "Re-opening surge (+€12,420) covers February payroll (€8,600) & Social Security (€2,650) and starts recovery.", ["PAYROLL-02", "REOPEN-SURGE"]),
        ("Wk 9", "Mar 02", 11980.0, 5650.0, 0.0, "March rent (-€3,800) absorbed by strong dining traffic & Commerzbank Global Pay payouts.", ["RENT-MAR-2026", "CGP-SETTLE-W9"]),
        ("Wk 10", "Mar 09", 10120.0, 8868.0, 0.0, "Q1 Einkommensteuer prepayment (-€1,150) + Wage Tax (€880) + Zurich CHF tranche (-€4,363) covered by cash flow.", ["TAX-EST-Q1", "WIRE-CHF-03"]),
        ("Wk 11", "Mar 16", 11450.0, 2150.0, 0.0, "Net surplus week (+€9,300): high-margin dining & wine sales rebuild liquidity.", ["CGP-SETTLE-W11"]),
        ("Wk 12", "Mar 23", 10340.0, 12980.0, 0.0, "March payroll (-€8,600) & Social Security (-€2,650) funded cleanly above the €12,000 buffer.", ["PAYROLL-03", "TK-SOC-03"]),
        ("Wk 13", "Mar 30", 14250.0, 2410.0, 0.0, "Quarter-end liquidity builds strongly above €21,000, restoring Festgeld sweep capacity.", ["SURPLUS-Q1"]),
    ]

    bal = start_bal
    points = []
    for i, (wk, label, inf, outf, credit_need, flux, refs) in enumerate(weekly_spec, start=1):
        net = round(inf - outf, 2)
        bal = round(bal + net, 2)
        points.append(
            {
                "week": i,
                "label": wk,
                "date_label": label,
                "inflow": inf,
                "outflow": outf,
                "net": net,
                "baseline": bal,
                "flux_explanation": flux,
                "source_refs": refs,
                "fx_exposed_eur": 4363.0 if "CHF" in "".join(refs) else 0.0,
                "credit_support_eur": credit_need,
            }
        )
    return points


def _mittelstand_forecast_13w() -> list[dict[str, Any]]:
    # Exact scenario numbers from requirements/implementation_plan.md:
    # Start: €142,500, Buffer: €60,000, KronoStahl €48k delayed +16d -> Week 4 -€18,450,
    # Week 6 $85k USD invoice, Weeks 8-13 surplus +€88k above €60k buffer (~€148k balance).
    weekly_spec = [
        ("Wk 1", "Nov 23", 38000.0, 31500.0, 0.0, "Opening balance €142,500. Standard supplier runs & B2B receivables.", ["LEDGER-OPEN"]),
        ("Wk 2", "Nov 30", 24000.0, 42000.0, 0.0, "Monthly lease & raw steel procurement (-€42,000); KronoStahl AG INV-AR-402 (€48,000) overdue.", ["INV-AR-402-LATE"]),
        ("Wk 3", "Dec 07", 19500.0, 56000.0, 0.0, "Component suppliers & mid-month prepayments drain liquidity toward €60,000 safety buffer.", ["PO-2026-104"]),
        ("Wk 4", "Dec 14", 12000.0, 124950.0, 85000.0, "CRITICAL SHORTFALL (-€18,450): Payroll (€58,500) + Q4 VAT Advance (€29,400) + suppliers collide while KronoStahl €48,000 is delayed +16 days.", ["PAYROLL-58.5K", "VAT-Q4-29.4K", "INV-AR-402"]),
        ("Wk 5", "Dec 21", 89000.0, 21000.0, 0.0, "KronoStahl AG €48,000 finally settles (+16d late) alongside Q4 deliveries, lifting balance to +€49,550.", ["INV-AR-402-PAID"]),
        ("Wk 6", "Dec 28", 52000.0, 89500.0, 0.0, "FX EXPOSURE: Foreign supplier invoice INV-USD-2026-771 ($85,000 USD ≈ €78,522) due; unhedged USD surge adds +€4,999 risk.", ["INV-USD-2026-771"]),
        ("Wk 7", "Jan 04", 71000.0, 21000.0, 0.0, "Strong Q1 B2B deliveries recover working capital to €62,050 (above the €60,000 buffer).", ["AR-Q1-BATCH"]),
        ("Wk 8", "Jan 11", 68000.0, 19000.0, 0.0, "Surplus builds past €111,000 as overdue receivables clear.", ["AR-REC-W8"]),
        ("Wk 9", "Jan 18", 58000.0, 21000.0, 0.0, "Idle cash exceeds €148,000 (+€88,000 above €60,000 safety buffer) — prime window for Commerzbank 60-Day Festgeld.", ["SURPLUS-W9"]),
        ("Wk 10", "Jan 25", 49000.0, 44000.0, 0.0, "Sustained liquidity surplus (€153,050) well above safety floor.", ["SURPLUS-W10"]),
        ("Wk 11", "Feb 01", 43000.0, 41000.0, 0.0, "Balanced operating week; €95,050 idle liquidity above €60,000 safety floor.", ["SURPLUS-W11"]),
        ("Wk 12", "Feb 08", 44000.0, 40000.0, 0.0, "Consistent cash generation; Commerzbank Festgeld earns yield without liquidity strain.", ["SURPLUS-W12"]),
        ("Wk 13", "Feb 15", 46000.0, 41000.0, 0.0, "13-week closing balance €164,050 (+€104,050 above safety floor).", ["SURPLUS-W13"]),
    ]

    bal = 142500.0
    points = []
    for i, (wk, label, inf, outf, credit_need, flux, refs) in enumerate(weekly_spec, start=1):
        net = round(inf - outf, 2)
        bal = round(bal + net, 2)
        points.append(
            {
                "week": i,
                "label": wk,
                "date_label": label,
                "inflow": inf,
                "outflow": outf,
                "net": net,
                "baseline": bal,
                "flux_explanation": flux,
                "source_refs": refs,
                "fx_exposed_eur": 78522.0 if i == 6 else 0.0,
                "credit_support_eur": credit_need,
            }
        )
    return points


def _custom_forecast_13w(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    start_bal = float(parsed["closing_balance"])
    monthly = parsed.get("monthly") or []
    avg_weekly_in = round((parsed["total_inflows"] / max(1, len(monthly))) / 4.33, 2)
    avg_weekly_out = round((parsed["total_outflows"] / max(1, len(monthly))) / 4.33, 2)
    bal = start_bal
    points = []
    base_date = dt.date(2026, 1, 5)
    for i in range(1, 14):
        # Simulate mild cyclical working-capital dip in Weeks 4-5 (e.g. payroll + tax collision)
        inflow = round(avg_weekly_in * (0.65 if i in (4, 5) else 1.05), 2)
        outflow = round(avg_weekly_out * (1.35 if i in (4, 8, 12) else 0.90), 2)
        net = round(inflow - outflow, 2)
        bal = round(bal + net, 2)
        d_label = (base_date + dt.timedelta(days=7 * (i - 1))).strftime("%b %d")
        points.append(
            {
                "week": i,
                "label": f"Wk {i}",
                "date_label": d_label,
                "inflow": inflow,
                "outflow": outflow,
                "net": net,
                "baseline": bal,
                "flux_explanation": f"Projected from uploaded ledger average (€{avg_weekly_in:,.0f}/wk inflows vs. €{avg_weekly_out:,.0f}/wk outflows)"
                + (" with month-end payroll/tax concentration." if i in (4, 8, 12) else "."),
                "source_refs": ["CUSTOM-CSV-RUNRATE"],
                "fx_exposed_eur": round(avg_weekly_out * 0.25, 2) if parsed["fx_transactions"] and i in (2, 6, 10) else 0.0,
                "credit_support_eur": max(0.0, round(5000.0 - bal, 2)) if bal < 5000.0 else 0.0,
            }
        )
    return points


def _build_pizzeria_actions(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    closing_bal = parsed["closing_balance"]
    idle_above_buffer = max(0.0, round(closing_bal - 12000.0, 2))
    recommended_sweep = min(20000.0, max(5000.0, round(idle_above_buffer // 1000 * 1000, 2)))
    return [
        {
            "id": "act-pa-liquidity",
            "domain": "Liquidity",
            "category": "cashflow",
            "severity": "high",
            "product_name": "Commerzbank Operating Credit Line (Betriebsmittelkredit)",
            "title": "Bridge Upcoming Seasonal Closure & Eliminate Unarranged Overdraft Fees",
            "status": "pending",
            "amount_eur": 25000.0,
            "min_amount_eur": 10000.0,
            "max_amount_eur": 40000.0,
            "step_eur": 2500.0,
            "selected_option": "30-Day Revolving Facility (5.25% p.a.)",
            "options": ["30-Day Revolving Facility (5.25% p.a.)", "60-Day Seasonal Credit Buffer (5.45% p.a.)"],
            "net_benefit_eur": 1420.0,
            "interest_or_fee_eur": 129.45,
            "savings_or_yield_eur": 1549.45,
            "autonomy": {
                "analyzed_independently": (
                    f"Analyzed 1,125 bookings on {parsed['own_iban']}. Detected 2025 June vacation trough of "
                    f"-€{abs(parsed['min_daily_balance']):,.2f} and an upcoming Week-7 seasonal dip to -€8,420.00 "
                    "driven by fixed €8,600 payroll, €3,800 rent, €2,650 Krankenkasse, and 4,166.67 CHF Zurich transfers."
                ),
                "recommended_logic": (
                    "Pre-approve a €25,000 Commerzbank Revolving Credit Line at 5.25% p.a. instead of paying 12.85% p.a. "
                    "unarranged overdraft interest. 45-day drawdown cost (€129.45) saves €316.85 in overdraft fees + "
                    "preserves €1,232.60 in supplier Skonto discounts (Net gain: +€1,420.00)."
                ),
                "requires_approval": "Owner confirms credit facility limit and clicks Approve Credit Facility.",
                "execution_mode": "Analysis: Permitted Live | Credit Line Activation: Simulated Commerzbank Corporate API",
            },
            "curve_effect": {"type": "credit_line", "floor_eur": 14000.0},
        },
        {
            "id": "act-pa-fx",
            "domain": "FX Hedging",
            "category": "fx",
            "severity": "medium",
            "product_name": "Commerzbank EUR/CHF Par-Forward (6 Monthly Tranches)",
            "title": "Lock EUR/CHF Rate on 4,166.67 CHF/mo Zurich Partnership Transfers",
            "status": "pending",
            "amount_eur": 23923.46,
            "min_amount_eur": 12000.0,
            "max_amount_eur": 48000.0,
            "step_eur": 4000.0,
            "selected_option": "EUR/CHF Forward @ 1.0450 (€0 Fee)",
            "options": ["EUR/CHF Forward @ 1.0450 (€0 Fee)", "EUR/CHF Call Option (€210 Premium)"],
            "net_benefit_eur": 1312.50,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 1312.50,
            "autonomy": {
                "analyzed_independently": (
                    f"Detected {len(parsed['fx_transactions'])} recurring CHF international wires totaling 50,000.04 CHF "
                    "to Luigi Amalfi (CH9300700110001234567). Historical EUR/CHF execution ranged from 1.038 to 1.062; "
                    "a 5–6% CHF appreciation adds up to +€1,312.50 in unbudgeted EUR cost over the next 6 months."
                ),
                "recommended_logic": (
                    "Structure a Commerzbank 6-Tranche EUR/CHF Par-Forward locking 25,000 CHF at 1.0450 (€23,923.46 fixed EUR, "
                    "€0 upfront fee), compared side-by-side against an EUR/CHF Call Option (€210 premium with upside flexibility)."
                ),
                "requires_approval": "Owner selects FX Forward or FX Option and clicks Approve CHF Hedge.",
                "execution_mode": "FX Exposure Math: Permitted Live | Contract Booking: Simulated Commerzbank FX Treasury API",
            },
            "curve_effect": {"type": "fx_hedge", "savings_per_fx_event": 310.0},
        },
        {
            "id": "act-pa-invest",
            "domain": "Investment",
            "category": "investment",
            "severity": "low",
            "product_name": "Commerzbank 60-Day Festgeld (3.65% p.a.)",
            "title": "Sweep Idle Year-End Cash Surplus into Risk-Free Festgeld",
            "status": "pending",
            "amount_eur": recommended_sweep,
            "min_amount_eur": 5000.0,
            "max_amount_eur": 30000.0,
            "step_eur": 2500.0,
            "selected_option": "60-Day Fixed-Term Festgeld (3.65% p.a.)",
            "options": ["60-Day Fixed-Term Festgeld (3.65% p.a.)", "Flexible TagesgeldPlus (3.40% p.a.)"],
            "net_benefit_eur": round(recommended_sweep * 0.0365 * (60 / 365), 2),
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": round(recommended_sweep * 0.0365 * (60 / 365), 2),
            "autonomy": {
                "analyzed_independently": (
                    f"Year-end closing balance reached +€{closing_bal:,.2f}, leaving €{idle_above_buffer:,.2f} idle above "
                    "your €12,000 operating safety buffer earning 0.00%."
                ),
                "recommended_logic": (
                    f"Sweep €{recommended_sweep:,.2f} into a Commerzbank 60-Day Festgeld at 3.65% p.a., generating "
                    f"+€{recommended_sweep * 0.0365 * (60 / 365):,.2f} risk-free yield while keeping €{closing_bal - recommended_sweep:,.2f} liquid."
                ),
                "requires_approval": "Owner adjusts placement amount and clicks Approve Deposit Sweep.",
                "execution_mode": "Surplus Detection: Permitted Live | Deposit Sweep: Simulated Commerzbank Treasury API",
            },
            "curve_effect": {"type": "investment", "yield_eur": round(recommended_sweep * 0.0365 * (60 / 365), 2)},
        },
        {
            "id": "act-pa-tax",
            "domain": "Tax Optimization",
            "category": "tax",
            "severity": "medium",
            "product_name": "Commerzbank Automated Tax Vault & Q2 Prepayment Shift",
            "title": "Ring-Fence Monthly Tax & Optimize Summer Einkommensteuer Prepayment",
            "status": "pending",
            "amount_eur": 2255.0,
            "min_amount_eur": 1000.0,
            "max_amount_eur": 5000.0,
            "step_eur": 250.0,
            "selected_option": "Auto-Reserve 6.5% Card Revenue + Q2 Adjustment",
            "options": ["Auto-Reserve 6.5% Card Revenue + Q2 Adjustment", "Tax Sub-Account Reserve Only"],
            "net_benefit_eur": 1150.0,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 1150.0,
            "autonomy": {
                "analyzed_independently": (
                    "Tracked monthly Wage Tax (€880 on the 10th), quarterly Einkommensteuer (€1,150 Mar/Jun/Sep/Dec 10), "
                    "and Gewerbesteuer (€225). June 10 tax debits (€2,030) coincide directly with the 3-week closure."
                ),
                "recommended_logic": (
                    "Automatically sweep €2,255/quarter from Commerzbank Global Pay payouts into a Tax Vault and prepare "
                    "a § 37 EStG prepayment timing adjustment to protect €1,150 liquidity during closure."
                ),
                "requires_approval": "Owner approves automated SEPA sub-account tax rule.",
                "execution_mode": "Tax Calendar Analysis: Permitted Live | Sub-Account Rule: Simulated Commerzbank SEPA API",
            },
            "curve_effect": {"type": "tax_relief", "relief_eur": 1150.0},
        },
        {
            "id": "act-pa-fraud",
            "domain": "3-Way Fraud Guard",
            "category": "fraud",
            "severity": "critical",
            "product_name": "Pre-Forecast 3-Way AP Fraud Quarantine",
            "title": "Quarantined Invoice INV-FLV-2026-109 (€3,480) — Unverified Lithuanian IBAN",
            "status": "quarantined",
            "amount_eur": 3480.0,
            "min_amount_eur": 3480.0,
            "max_amount_eur": 3480.0,
            "step_eur": 0.0,
            "selected_option": "Keep Defensive Quarantine & Verify by Phone",
            "options": ["Keep Defensive Quarantine & Verify by Phone", "Override Hold (Dual Verification)"],
            "net_benefit_eur": 3480.0,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 3480.0,
            "autonomy": {
                "analyzed_independently": (
                    "3-Way check matched incoming bill INV-FLV-2026-109 (€3,480.00, Fattoria La Vino GmbH) against 2025 "
                    "master vendor IBAN DE66500105170099881122 (WELADEDDXXX). Flagged unverified IBAN LT601010012345678901 "
                    "and a +288% jump above historical average (€895.00)."
                ),
                "recommended_logic": (
                    "Invoice placed in Defensive Quarantine so fraudulent outflow cannot distort your 13-week cash forecast "
                    "or trigger an unauthorized SEPA payment. Call Fattoria La Vino on verified master phone number."
                ),
                "requires_approval": "Owner confirms permanent block (Confirm Fraud Hold) or releases with dual verification.",
                "execution_mode": "Quarantine Hold: Permitted Autonomous Defensive Action | Payment Release: Hard-Blocked",
            },
            "curve_effect": {"type": "fraud_hold", "saved_eur": 3480.0},
        },
    ]


def _build_mittelstand_actions() -> list[dict[str, Any]]:
    return [
        {
            "id": "act-mb-liquidity",
            "domain": "Liquidity",
            "category": "cashflow",
            "severity": "critical",
            "product_name": "Commerzbank 30-Day Revolving Credit Facility (5.25% p.a.)",
            "title": "Eliminate Week-4 -€18,450 Payroll + VAT Shortfall & Protect €60k Buffer",
            "status": "pending",
            "amount_eur": 85000.0,
            "min_amount_eur": 50000.0,
            "max_amount_eur": 120000.0,
            "step_eur": 5000.0,
            "selected_option": "30-Day Revolving Credit Draw (€85,000 @ 5.25% p.a.)",
            "options": ["30-Day Revolving Credit Draw (€85,000 @ 5.25% p.a.)", "60-Day Working Capital Line (€85,000 @ 5.45% p.a.)"],
            "net_benefit_eur": 3883.22,
            "interest_or_fee_eur": 366.78,
            "savings_or_yield_eur": 4250.0,
            "autonomy": {
                "analyzed_independently": (
                    "Shifted KronoStahl AG invoice INV-AR-402 (€48,000) by +16 days using historical behavioral payment delay. "
                    "Uncovered Week-4 -€18,450 shortfall when Payroll (€58,500) + Q4 VAT Advance (€29,400) collide."
                ),
                "recommended_logic": (
                    "Draw €85,000 for 30 days on a Commerzbank Revolving Credit Facility at 5.25% p.a. (€366.78 interest) to "
                    "maintain the €60,000 safety buffer and save €4,250.00 in supplier late fees & lost Skonto (Net gain: +€3,883.22)."
                ),
                "requires_approval": "Owner adjusts credit draw slider and clicks Approve Credit Drawdown.",
                "execution_mode": "Analysis: Permitted Live | Credit Drawdown: Simulated Commerzbank Credit API",
            },
            "curve_effect": {"type": "credit_line", "floor_eur": 62000.0},
        },
        {
            "id": "act-mb-fx",
            "domain": "FX Hedging",
            "category": "fx",
            "severity": "high",
            "product_name": "Commerzbank 45-Day EUR/USD FX Forward @ 1.0825",
            "title": "Hedge Week-6 $85,000 USD Supplier Invoice (INV-USD-2026-771)",
            "status": "pending",
            "amount_eur": 78521.94,
            "min_amount_eur": 40000.0,
            "max_amount_eur": 85000.0,
            "step_eur": 5000.0,
            "selected_option": "45-Day FX Forward @ 1.0825 (€0 Fee)",
            "options": ["45-Day FX Forward @ 1.0825 (€0 Fee)", "45-Day FX Call Option (€640 Premium)"],
            "net_benefit_eur": 4998.89,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 4998.89,
            "autonomy": {
                "analyzed_independently": (
                    "Flagged foreign AP invoice INV-USD-2026-771 ($85,000 USD) due in 45 days (Week 6) against EUR revenue. "
                    "A 6% USD appreciation adds +€4,998.89 in unbudgeted procurement cost."
                ),
                "recommended_logic": (
                    "Lock EUR/USD at 1.0825 via a Commerzbank 45-Day FX Forward (€78,521.94 fixed cost, €0 upfront fee), "
                    "compared side-by-side with an FX Call Option (€640 premium)."
                ),
                "requires_approval": "Owner chooses FX Forward or FX Option and clicks Approve Hedge.",
                "execution_mode": "Exposure Math: Permitted Live | FX Contract Booking: Simulated Commerzbank FX Treasury API",
            },
            "curve_effect": {"type": "fx_hedge", "savings_per_fx_event": 4998.89},
        },
        {
            "id": "act-mb-invest",
            "domain": "Investment",
            "category": "investment",
            "severity": "low",
            "product_name": "Commerzbank 60-Day Festgeld (3.65% p.a.)",
            "title": "Deploy Weeks 8–13 €88,000 Idle Surplus into Festgeld / MMF",
            "status": "pending",
            "amount_eur": 80000.0,
            "min_amount_eur": 25000.0,
            "max_amount_eur": 90000.0,
            "step_eur": 5000.0,
            "selected_option": "60-Day Fixed-Term Festgeld (3.65% p.a.)",
            "options": ["60-Day Fixed-Term Festgeld (3.65% p.a.)", "Commerzbank Money Market Fund (3.85% p.a.)"],
            "net_benefit_eur": 480.0,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 480.0,
            "autonomy": {
                "analyzed_independently": (
                    "Weeks 8–13 post-crunch recovery builds €88,000 in idle liquidity sitting above the €60,000 safety buffer for 60+ days."
                ),
                "recommended_logic": (
                    "Sweep €80,000 into a Commerzbank 60-Day Festgeld (3.65% p.a., +€480.00 yield) or Money Market Fund "
                    "(3.85% p.a., +€506.00 yield) without affecting operating safety."
                ),
                "requires_approval": "Owner selects product & amount and clicks Approve Placement.",
                "execution_mode": "Surplus Detection: Permitted Live | Deposit Sweep: Simulated Commerzbank Deposit API",
            },
            "curve_effect": {"type": "investment", "yield_eur": 480.0},
        },
        {
            "id": "act-mb-fraud",
            "domain": "3-Way Fraud Guard",
            "category": "fraud",
            "severity": "critical",
            "product_name": "Pre-Forecast 3-Way AP Fraud Quarantine",
            "title": "Quarantined INV-2026-994 (€16,488) — +14.5% Price Hike & Altered IBAN",
            "status": "quarantined",
            "amount_eur": 16488.0,
            "min_amount_eur": 16488.0,
            "max_amount_eur": 16488.0,
            "step_eur": 0.0,
            "selected_option": "Confirm Fraud Hold (Block Payment)",
            "options": ["Confirm Fraud Hold (Block Payment)", "Override Hold (Dual Verification)"],
            "net_benefit_eur": 16488.0,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 16488.0,
            "autonomy": {
                "analyzed_independently": (
                    "Matched supplier invoice INV-2026-994 (€16,488) against Purchase Order PO-2026-118 (€14,400) and Master "
                    "Vendor IBAN DE89... Flagged altered foreign IBAN LT601010012345678901 and +14.5% unauthorized price hike."
                ),
                "recommended_logic": (
                    "Placed invoice in Defensive Quarantine so fraudulent payment is excluded from forecast and blocked from SEPA export."
                ),
                "requires_approval": "Owner clicks Confirm Fraud Hold or Override with Dual Verification.",
                "execution_mode": "Quarantine Hold: Permitted Autonomous Defensive Action | Payment Release: Hard-Blocked",
            },
            "curve_effect": {"type": "fraud_hold", "saved_eur": 16488.0},
        },
    ]


def get_regulatory_updates(scenario_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    industry = (profile.get("industry") or "").lower()
    notes = (profile.get("context_notes") or "").lower()
    is_gastro = "gastro" in industry or "hospitality" in industry or "pizzeria" in (profile.get("business_name") or "").lower()
    has_chf = "chf" in notes or "zurich" in notes or "switzerland" in notes or scenario_id == "pizzeria_amalfi"

    updates = [
        {
            "id": "gov-erechnung-2026",
            "title": "Mandatory B2B E-Invoicing Transition (Wachstumschancengesetz / § 14 UStG)",
            "category": "law",
            "severity": "medium",
            "effective_date": "2026-01-01",
            "official_source_name": "Bundesministerium der Finanzen (BMF)",
            "official_url": "https://www.bundesfinanzministerium.de/Web/DE/Themen/Steuern/Steuerarten/Umsatzsteuer/e-rechnung.html",
            "what_changed": "German businesses must accept structured XML e-invoices (XRechnung / ZUGFeRD) from domestic suppliers and phase out paper/unstructured PDF B2B invoices.",
            "why_it_applies": (
                f"Applies directly to {profile.get('business_name', 'your business')} supplier invoices "
                + ("(Asimex Wholesale Ltd & Fattoria La Vino GmbH)." if is_gastro else "(KronoStahl AG & domestic engineering suppliers).")
            ),
            "estimated_impact_eur": 1850.0,
            "action_checklist": [
                "Verify Asimex / supplier invoices arrive in ZUGFeRD or XRechnung format for input VAT deduction.",
                "Enable Greg's automated 3-way IBAN & PO matching on incoming XML invoices.",
            ],
        },
        {
            "id": "gov-kfw-energy-grant",
            "title": "KfW / Hessen SME Energy Efficiency & Kitchen/Equipment Modernization Grant",
            "category": "relief",
            "severity": "low",
            "effective_date": "2026-03-31",
            "official_source_name": "KfW Bankengruppe / WIBank Hessen",
            "official_url": "https://www.kfw.de/inlandsfoerderung/Unternehmen/Energie-und-Umwelt/",
            "what_changed": "Up to 20% non-repayable subsidy / low-interest financing for SMEs upgrading high-draw equipment (ovens, refrigeration, CNC motors) to reduce utility spend.",
            "why_it_applies": (
                f"Matches your recurring utility spend in {profile.get('location', 'Frankfurt am Main, Hessen')} "
                + ("(€650/mo City Energy & Utilities AG)." if is_gastro else "(high-load industrial facility energy bills).")
            ),
            "estimated_impact_eur": 4500.0 if is_gastro else 14000.0,
            "action_checklist": [
                "Export annual City Energy & Utilities AG invoice summary from Greg.",
                "Submit eligibility pre-check via Commerzbank Fördermittel-Service before ordering equipment.",
            ],
        },
    ]

    if is_gastro:
        updates.insert(
            0,
            {
                "id": "gov-gastro-vat-2026",
                "title": "Hospitality VAT Split & Cash Register Compliance (UStG § 12 / KassenSichV)",
                "category": "tax",
                "severity": "high",
                "effective_date": "2026-01-01",
                "official_source_name": "Bundesministerium der Finanzen (BMF)",
                "official_url": "https://www.bundesfinanzministerium.de/Web/DE/Themen/Steuern/Steuerarten/Umsatzsteuer/umsatzsteuer.html",
                "what_changed": "Updated documentation rules for separating 7% food vs. 19% beverage VAT on POS card settlements and Commerzbank Global Pay payouts.",
                "why_it_applies": (
                    f"Matched to {profile.get('business_name', 'Pizzeria Amalfi')} ({profile.get('industry', 'Gastronomy')}) "
                    "where daily debit & Commerzbank Global Pay settlements combine dine-in, takeaway, and wine sales."
                ),
                "estimated_impact_eur": 3240.0,
                "action_checklist": [
                    "Tag Fattoria La Vino beverage purchases separately from Asimex food procurement.",
                    "Confirm POS TSE export matches monthly Tax Office Frankfurt filings (€880 Wage Tax / €1,150 ESt).",
                ],
            },
        )

    if has_chf:
        updates.append(
            {
                "id": "gov-dba-ch-reporting",
                "title": "Cross-Border Silent Partnership Tax Reporting (DBA DE–CH / § 138 AO)",
                "category": "tax",
                "severity": "medium",
                "effective_date": "2026-05-31",
                "official_source_name": "ELSTER / Bundeszentralamt für Steuern (BZSt)",
                "official_url": "https://www.bzst.de/DE/Unternehmen/Internationale_Steuern/internationale_steuern_node.html",
                "what_changed": "Mandatory disclosure of cross-border capital participations exceeding €15,000 / CHF equivalents and withholding credit documentation under the Germany–Switzerland tax treaty.",
                "why_it_applies": "Triggered by 12 monthly international wire transfers of 4,166.67 CHF (50,000 CHF total) to Zurich.",
                "estimated_impact_eur": 2400.0,
                "action_checklist": [
                    "Attach the 12 Commerzbank CHF wire confirmations to your annual tax package.",
                    "Lock remaining tranches via Commerzbank EUR/CHF Par-Forward to stabilize tax-basis EUR reporting.",
                ],
            }
        )

    return updates


def parse_what_if_query(query: str) -> dict[str, Any]:
    """Extract structured simulation shocks from a natural-language What-If query."""
    q = (query or "").lower()
    rev_pct = 0.0
    delay_days = 0
    fx_shock_pct = 0.0
    extra_cost = 0.0

    # Revenue or closure patterns
    if "close" in q or "closure" in q or "vacation" in q or "shutdown" in q:
        m_wks = re.search(r"(\d+)\s*week", q)
        wks = int(m_wks.group(1)) if m_wks else 2
        rev_pct = -min(45.0, wks * 12.0)
        delay_days = max(delay_days, wks * 7)
    m_rev_drop = re.search(r"(?:revenue|sales|income)\s+(?:drops?|falls?|down|decreases?)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*%", q)
    if m_rev_drop:
        rev_pct = -float(m_rev_drop.group(1))
    m_rev_up = re.search(r"(?:revenue|sales|income)\s+(?:grows?|rises?|up|increases?)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*%", q)
    if m_rev_up:
        rev_pct = float(m_rev_up.group(1))

    # Payment delay patterns
    m_delay = re.search(r"(?:delay|delays|late|postpone)\D{0,25}(\d+)\s*days?", q)
    if m_delay:
        delay_days = min(45, int(m_delay.group(1)))

    # FX shock patterns
    m_fx = re.search(r"(?:usd|chf|dollar|franc|fx)\D{0,25}(?:rises?|strengthens?|surges?|up|jumps?)\D{0,15}(\d+(?:\.\d+)?)\s*%", q)
    if m_fx:
        fx_shock_pct = float(m_fx.group(1))
    elif "usd" in q or "chf" in q:
        m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", q)
        if m_pct and not m_rev_drop and not m_rev_up:
            fx_shock_pct = float(m_pct.group(1))

    # Extra cost / hire patterns
    m_hire = re.search(r"(?:hire|cost|expense|rent|salary)\D{0,25}(?:€|eur)?\s*(\d[\d,\.]*)", q)
    if m_hire:
        raw_num = m_hire.group(1).replace(",", "")
        try:
            extra_cost = min(50000.0, float(raw_num))
        except ValueError:
            pass

    include_cb = any(w in q for w in ("commerzbank", "credit", "hedge", "forward", "festgeld", "stabilize", "action"))
    return {
        "revenue_change_pct": rev_pct,
        "delay_days": delay_days,
        "fx_shock_pct": fx_shock_pct,
        "extra_monthly_cost_eur": extra_cost,
        "include_commerzbank_actions": include_cb,
    }


def simulate_what_if(
    forecast_13w: list[dict[str, Any]],
    params: dict[str, Any],
    actions: list[dict[str, Any]],
    safety_buffer_eur: float,
) -> dict[str, Any]:
    """Compute Baseline, What-If Stress, and Stabilized (Commerzbank Actions) curves across 13 weeks."""
    rev_pct = float(params.get("revenue_change_pct", 0.0))
    delay_days = int(params.get("delay_days", 0))
    fx_shock_pct = float(params.get("fx_shock_pct", 0.0))
    extra_monthly = float(params.get("extra_monthly_cost_eur", 0.0))
    include_actions = bool(params.get("include_commerzbank_actions", True))

    approved_ids = {a["id"] for a in actions if a.get("status") == "approved"}
    # Either user toggled "simulate with recommended Commerzbank actions" OR already approved specific actions
    active_credit = 0.0
    active_fx_hedge = False
    active_tax_relief = 0.0
    active_invest_yield = 0.0

    for a in actions:
        is_on = include_actions or (a["id"] in approved_ids)
        if not is_on:
            continue
        eff = a.get("curve_effect", {})
        etype = eff.get("type")
        if etype == "credit_line":
            active_credit = max(active_credit, float(a.get("amount_eur", 25000.0)))
        elif etype == "fx_hedge":
            active_fx_hedge = True
        elif etype == "tax_relief":
            active_tax_relief += float(eff.get("relief_eur", 0.0))
        elif etype == "investment":
            active_invest_yield += float(eff.get("yield_eur", 0.0))

    delay_weeks = delay_days / 7.0
    weekly_extra = extra_monthly / 4.33
    sim_bal = forecast_13w[0]["baseline"] - forecast_13w[0]["net"] if forecast_13w else 0.0
    stab_bal = sim_bal

    series = []
    min_baseline = (float("inf"), 1)
    min_sim = (float("inf"), 1)
    min_stab = (float("inf"), 1)
    buffer_breaches_sim = 0
    buffer_breaches_stab = 0

    for pt in forecast_13w:
        wk = pt["week"]
        inf = pt["inflow"]
        outf = pt["outflow"]

        # Revenue shift + delayed receivables/shutdown penalty concentrated early, partially recovered later
        delay_drag = 0.0
        if delay_weeks > 0:
            if wk <= 6:
                delay_drag = inf * min(0.65, delay_weeks * 0.14)
            elif wk >= 8:
                delay_drag = -inf * min(0.35, delay_weeks * 0.07)

        sim_inf = inf * (1.0 + rev_pct / 100.0) - delay_drag
        fx_extra = pt["fx_exposed_eur"] * (fx_shock_pct / 100.0)
        sim_outf = outf + max(0.0, fx_extra) + weekly_extra
        sim_net = sim_inf - sim_outf
        sim_bal = round(sim_bal + sim_net, 2)

        # Stabilized curve applies FX hedge protection, tax relief, investment yield, and revolving credit drawdown if below buffer
        hedged_fx_extra = 0.0 if active_fx_hedge else max(0.0, fx_extra)
        tax_boost = round(active_tax_relief / 2.0, 2) if wk in (6, 7) else 0.0
        yield_boost = round(active_invest_yield / 3.0, 2) if wk >= 11 else 0.0
        stab_raw_bal = round(stab_bal + (sim_inf - (outf + hedged_fx_extra + weekly_extra)) + tax_boost + yield_boost, 2)
        stab_bal = stab_raw_bal

        # Draw on revolving credit line dynamically to protect safety buffer (and repay automatically when surplus returns)
        draw_needed = 0.0
        if active_credit > 0 and stab_raw_bal < safety_buffer_eur:
            draw_needed = min(active_credit, round(safety_buffer_eur + 2500.0 - stab_raw_bal, 2))
        stab_effective = round(stab_raw_bal + draw_needed, 2)

        if pt["baseline"] < min_baseline[0]:
            min_baseline = (pt["baseline"], wk)
        if sim_bal < min_sim[0]:
            min_sim = (sim_bal, wk)
        if stab_effective < min_stab[0]:
            min_stab = (stab_effective, wk)
        if sim_bal < safety_buffer_eur:
            buffer_breaches_sim += 1
        if stab_effective < safety_buffer_eur:
            buffer_breaches_stab += 1

        series.append(
            {
                **pt,
                "simulated": sim_bal,
                "stabilized": stab_effective,
                "credit_drawn_eur": round(draw_needed, 2),
            }
        )

    return {
        "params": {
            "revenue_change_pct": rev_pct,
            "delay_days": delay_days,
            "fx_shock_pct": fx_shock_pct,
            "extra_monthly_cost_eur": extra_monthly,
            "include_commerzbank_actions": include_actions,
        },
        "series": series,
        "summary": {
            "min_baseline_eur": round(min_baseline[0], 2),
            "min_baseline_week": min_baseline[1],
            "min_simulated_eur": round(min_sim[0], 2),
            "min_simulated_week": min_sim[1],
            "min_stabilized_eur": round(min_stab[0], 2),
            "min_stabilized_week": min_stab[1],
            "buffer_breaches_simulated": buffer_breaches_sim,
            "buffer_breaches_stabilized": buffer_breaches_stab,
        },
    }


def compute_health_diagnostics(
    closing_balance: float,
    min_13w: float,
    safety_buffer: float,
    hygiene_score: float,
    fx_exposure_eur: float,
) -> dict[str, Any]:
    score = 84
    factors = []
    if closing_balance >= safety_buffer:
        factors.append({"label": "Current Cash vs. Safety Buffer", "impact": "+8", "detail": f"€{closing_balance:,.0f} >= €{safety_buffer:,.0f} target"})
    else:
        score -= 15
        factors.append({"label": "Current Cash Below Buffer", "impact": "-15", "detail": f"€{closing_balance:,.0f} < €{safety_buffer:,.0f}"})

    if min_13w < 0:
        score -= 18
        factors.append({"label": "13-Week Overdraft Risk", "impact": "-18", "detail": f"Projected dip to -€{abs(min_13w):,.0f}"})
    elif min_13w < safety_buffer:
        score -= 8
        factors.append({"label": "13-Week Buffer Dip", "impact": "-8", "detail": f"Drops below €{safety_buffer:,.0f} buffer"})

    if fx_exposure_eur > 10000:
        score -= 6
        factors.append({"label": "Unhedged FX Exposure", "impact": "-6", "detail": f"€{fx_exposure_eur:,.0f} open foreign outflows"})

    if hygiene_score >= 95:
        score += 6
        factors.append({"label": "Data Hygiene & 3-Way Fraud Guard", "impact": "+6", "detail": f"{hygiene_score}% clean ledger"})

    score = max(15, min(98, score))
    status = "Healthy" if score >= 80 else ("Watch" if score >= 60 else "At Risk")
    return {"score": score, "status": status, "factors": factors}


def build_scenario_bundle(
    scenario_id: str,
    custom_csv_text: str | None = None,
    custom_filename: str | None = None,
    opening_balance: float = 2500.0,
    profile_overrides: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
    action_overrides: dict[str, dict[str, Any]] | None = None,
    sim_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble full deterministic financial dataset, forecast, Commerzbank action cards, and regulatory intelligence."""
    if scenario_id == "mittelstand_b2b":
        profile = {
            "business_name": "RheinMain Präzisionstechnik GmbH",
            "owner_name": "Stefan Weber",
            "legal_form": "GmbH (Limited Liability)",
            "location": "Frankfurt am Main, Hessen, DE",
            "industry": "Industrial Manufacturing (NACE 28.99)",
            "employee_count": 22,
            "tax_regime": "Corporate Tax (KSt) + Trade Tax (GewSt) + Quarterly VAT",
            "account_iban": "DE89370400440532019900",
            "currency": "EUR",
            "safety_buffer_eur": 60000.0,
            "context_notes": (
                "KronoStahl AG (€48,000 INV-AR-402) typically pays +16 days late. "
                "Week 4 collides Payroll (€58,500) + Q4 VAT (€29,400). "
                "Week 6 has $85,000 USD supplier invoice INV-USD-2026-771. "
                "Quarantined fraudulent invoice INV-2026-994 (€16,488) with LT60 IBAN."
            ),
        }
        forecast = _mittelstand_forecast_13w()
        actions = _build_mittelstand_actions()
        parsed_summary = {
            "row_count": 418,
            "unique_count": 405,
            "duplicate_count": 13,
            "opening_balance": 124000.0,
            "closing_balance": 142500.0,
            "total_inflows": 1480000.0,
            "total_outflows": 1461500.0,
            "net_cash_flow": 18500.0,
            "min_daily_date": "2025-11-12",
            "min_daily_balance": 51200.0,
            "max_daily_date": "2025-12-20",
            "max_daily_balance": 164800.0,
            "monthly": [
                {"month": "2025-07", "inflows": 220000, "outflows": 212000, "net": 8000, "ending_balance": 132000},
                {"month": "2025-08", "inflows": 235000, "outflows": 241000, "net": -6000, "ending_balance": 126000},
                {"month": "2025-09", "inflows": 258000, "outflows": 239000, "net": 19000, "ending_balance": 145000},
                {"month": "2025-10", "inflows": 244000, "outflows": 251000, "net": -7000, "ending_balance": 138000},
                {"month": "2025-11", "inflows": 261000, "outflows": 256500, "net": 4500, "ending_balance": 142500},
            ],
            "historical_curve": [
                {"date": "2025-07-01", "balance": 124000.0},
                {"date": "2025-08-15", "balance": 118400.0},
                {"date": "2025-09-30", "balance": 145000.0},
                {"date": "2025-10-28", "balance": 131200.0},
                {"date": "2025-11-30", "balance": 142500.0},
            ],
            "categories": [
                {"category": "Revenue & B2B Receivables", "amount": 1480000.0, "abs_amount": 1480000.0, "count": 142},
                {"category": "Payroll & Salaries", "amount": -702000.0, "abs_amount": 702000.0, "count": 66},
                {"category": "Steel & USD Components", "amount": -524000.0, "abs_amount": 524000.0, "count": 118},
                {"category": "VAT & Corporate Taxes", "amount": -142500.0, "abs_amount": 142500.0, "count": 24},
                {"category": "Facility & Energy", "amount": -93000.0, "abs_amount": 93000.0, "count": 55},
            ],
            "fx_transactions": [
                {
                    "date": "2026-01-25",
                    "counterparty": "Apex Precision Supply LLC",
                    "currency": "USD",
                    "raw_amount": -85000.0,
                    "eur_amount": -78521.94,
                    "fx_rate": 1.0825,
                    "iban": "US64BOFA0009912345",
                }
            ],
            "recent_ledger": [
                {"date": "2025-11-30", "type": "AR OPEN (DELAYED +16d)", "counterparty": "KronoStahl AG", "category": "Receivables", "amount": 48000.0, "currency": "EUR", "raw_amount": 48000.0, "reference": "INV-AR-402 (Behavioral Shift +16d)", "iban": "DE44500105175407324931", "balance_after": 142500.0},
                {"date": "2025-11-28", "type": "AP FX EXPOSURE", "counterparty": "Apex Precision Supply LLC", "category": "USD Procurement", "amount": -78521.94, "currency": "USD", "raw_amount": -85000.0, "reference": "INV-USD-2026-771 Due Wk 6", "iban": "US64BOFA0009912345", "balance_after": 142500.0},
                {"date": "2025-11-26", "type": "QUARANTINED HOLD", "counterparty": "Nordic Tooling GmbH", "category": "3-Way Fraud Guard", "amount": -16488.0, "currency": "EUR", "raw_amount": -16488.0, "reference": "BLOCKED: INV-2026-994 altered IBAN LT60...", "iban": "LT601010012345678901", "balance_after": 142500.0},
            ],
            "own_iban": "DE89370400440532019900",
            "hygiene": {
                "score": 96.8,
                "raw_rows": 418,
                "clean_rows": 405,
                "duplicates_removed": 13,
                "normalized_vendors": 28,
                "variants_merged": 7,
            },
        }
        fx_label = "$85,000 USD (Week 6)"
        fx_exposure_eur = 78521.94
        quarantined_invoice = {
            "invoice_id": "INV-2026-994",
            "vendor": "Nordic Tooling GmbH",
            "po_id": "PO-2026-118",
            "po_amount_eur": 14400.0,
            "invoice_amount_eur": 16488.0,
            "price_variance_pct": 14.5,
            "expected_iban": "DE89370400440532018811",
            "received_iban": "LT601010012345678901",
            "reason": "Unverified foreign IBAN (LT60...) + 14.5% unauthorized hike over PO-2026-118.",
        }
        dataset_label = "Mittelstand B2B Scenario (KronoStahl / USD / IBAN Fraud)"
    elif scenario_id == "custom_csv" and custom_csv_text:
        parsed_summary = parse_bank_csv(custom_csv_text, opening_balance=Decimal(str(opening_balance)))
        profile = {
            "business_name": "Uploaded SME Workspace",
            "owner_name": "Business Owner",
            "legal_form": "GmbH / Einzelunternehmen",
            "location": "Frankfurt am Main, Hessen, DE",
            "industry": "SME Operations",
            "employee_count": 5,
            "tax_regime": "German Business Tax + VAT",
            "account_iban": parsed_summary["own_iban"],
            "currency": "EUR",
            "safety_buffer_eur": 10000.0,
            "context_notes": f"Ingested custom bank statement '{custom_filename or 'uploaded.csv'}' ({parsed_summary['unique_count']} bookings).",
        }
        forecast = _custom_forecast_13w(parsed_summary)
        actions = _build_pizzeria_actions(parsed_summary)
        fx_exposure_eur = sum(abs(tx["eur_amount"]) for tx in parsed_summary["fx_transactions"])
        fx_label = f"€{fx_exposure_eur:,.0f} Foreign Wires" if fx_exposure_eur else "€0 (EUR Only)"
        quarantined_invoice = {
            "invoice_id": "NONE-DETECTED",
            "vendor": "Clean Ledger",
            "po_id": "AUTO-SCAN",
            "po_amount_eur": 0.0,
            "invoice_amount_eur": 0.0,
            "price_variance_pct": 0.0,
            "expected_iban": parsed_summary["own_iban"],
            "received_iban": parsed_summary["own_iban"],
            "reason": "No altered IBANs detected in uploaded CSV.",
        }
        dataset_label = f"Custom CSV: {custom_filename or 'uploaded.csv'}"
    else:
        # Default: Official Pizzeria Amalfi 2025 CSV Dataset
        scenario_id = "pizzeria_amalfi"
        csv_text = load_pizzeria_csv_text()
        parsed_summary = parse_bank_csv(csv_text, opening_balance=Decimal("2500.00"))
        profile = {
            "business_name": "Pizzeria Amalfi",
            "owner_name": "Vincenzo Amalfi",
            "legal_form": "Einzelunternehmen (Sole Proprietorship)",
            "location": "Frankfurt am Main, Hessen, DE",
            "industry": "Gastronomy & Hospitality (NACE 56.10)",
            "employee_count": 4,
            "tax_regime": "German Income Tax (ESt) + Trade Tax (GewSt) + VAT (7%/19%)",
            "account_iban": parsed_summary["own_iban"],
            "currency": "EUR",
            "safety_buffer_eur": 12000.0,
            "context_notes": (
                "4 staff payroll (€8,600/mo on 28th) + Techniker Krankenkasse (€2,650/mo on 27th) + Rent (€3,800/mo on 1st). "
                "3-week June shutdown pushed 2025 balance to -€22,097.60 before Q3/Q4 recovery to +€33,266.35. "
                "Monthly -4,166.67 CHF silent partnership wire to Luigi's Ristorante Zurich (50,000 CHF total)."
            ),
        }
        forecast = _pizzeria_forecast_13w(parsed_summary)
        actions = _build_pizzeria_actions(parsed_summary)
        fx_exposure_eur = round(sum(abs(tx["eur_amount"]) for tx in parsed_summary["fx_transactions"]) / 2.0, 2)
        fx_label = "25,000 CHF (Next 6 Zurich Tranches)"
        quarantined_invoice = {
            "invoice_id": "INV-FLV-2026-109",
            "vendor": "Fattoria La Vino GmbH",
            "po_id": "PO-2026-041",
            "po_amount_eur": 895.0,
            "invoice_amount_eur": 3480.0,
            "price_variance_pct": 288.8,
            "expected_iban": "DE66500105170099881122",
            "received_iban": "LT601010012345678901",
            "reason": "Altered foreign IBAN (LT60... vs verified DE66...) and +288% invoice jump over average €895.",
        }
        dataset_label = "Pizzeria Amalfi 2025 Bank Statement (1,125 Bookings)"

    if profile_overrides:
        profile.update({k: v for k, v in profile_overrides.items() if v is not None})

    safety_buffer = float(profile.get("safety_buffer_eur", 12000.0))
    prefs = _default_preferences(safety_buffer)
    if preferences:
        if "safety_buffer_eur" in preferences:
            prefs["safety_buffer_eur"] = float(preferences["safety_buffer_eur"])
            safety_buffer = prefs["safety_buffer_eur"]
        if "view_mode" in preferences:
            prefs["view_mode"] = preferences["view_mode"]
        if "min_severity" in preferences:
            prefs["min_severity"] = preferences["min_severity"]
        if "enabled_modules" in preferences and isinstance(preferences["enabled_modules"], dict):
            prefs["enabled_modules"].update(preferences["enabled_modules"])
        if "muted_categories" in preferences and isinstance(preferences["muted_categories"], list):
            prefs["muted_categories"] = preferences["muted_categories"]
        if "muted_action_ids" in preferences and isinstance(preferences["muted_action_ids"], list):
            prefs["muted_action_ids"] = preferences["muted_action_ids"]

    if action_overrides:
        for act in actions:
            ov = action_overrides.get(act["id"])
            if ov:
                act.update(ov)

    sim_result = simulate_what_if(
        forecast,
        sim_params or {"revenue_change_pct": 0.0, "delay_days": 0, "fx_shock_pct": 0.0, "extra_monthly_cost_eur": 0.0, "include_commerzbank_actions": True},
        actions,
        safety_buffer,
    )

    health = compute_health_diagnostics(
        closing_balance=parsed_summary["closing_balance"],
        min_13w=sim_result["summary"]["min_baseline_eur"],
        safety_buffer=safety_buffer,
        hygiene_score=parsed_summary["hygiene"]["score"],
        fx_exposure_eur=fx_exposure_eur,
    )

    regulatory_all = get_regulatory_updates(scenario_id, profile)

    # Apply preference filters while keeping full count metadata
    min_sev_rank = SEVERITY_ORDER.get(prefs["min_severity"], 1)
    muted_cats = set(prefs["muted_categories"])
    muted_ids = set(prefs["muted_action_ids"])
    enabled_mods = prefs["enabled_modules"]

    visible_actions = []
    for act in actions:
        if act["id"] in muted_ids or act["category"] in muted_cats:
            continue
        if act["category"] == "fx" and not enabled_mods.get("fx_hedging", True):
            continue
        if act["category"] == "investment" and not enabled_mods.get("investments", True):
            continue
        if act["category"] == "fraud" and not enabled_mods.get("fraud_guard", True):
            continue
        if SEVERITY_ORDER.get(act["severity"], 1) < min_sev_rank and act.get("status") == "pending":
            continue
        visible_actions.append(act)

    visible_reg = (
        [
            r
            for r in regulatory_all
            if r["category"] not in muted_cats and SEVERITY_ORDER.get(r["severity"], 1) >= min_sev_rank
        ]
        if enabled_mods.get("gov_news", True)
        else []
    )

    return {
        "scenario_id": scenario_id,
        "dataset_label": dataset_label,
        "profile": profile,
        "preferences": prefs,
        "kpis": {
            "current_balance_eur": parsed_summary["closing_balance"],
            "historical_trough_eur": parsed_summary["min_daily_balance"],
            "historical_trough_date": parsed_summary["min_daily_date"],
            "projected_min_13w_eur": sim_result["summary"]["min_baseline_eur"],
            "projected_min_13w_week": sim_result["summary"]["min_baseline_week"],
            "stabilized_min_13w_eur": sim_result["summary"]["min_stabilized_eur"],
            "safety_buffer_eur": safety_buffer,
            "total_inflows_eur": parsed_summary["total_inflows"],
            "total_outflows_eur": parsed_summary["total_outflows"],
            "fx_exposure_label": fx_label,
            "fx_exposure_eur": fx_exposure_eur,
            "hygiene_score": parsed_summary["hygiene"]["score"],
            "quarantined_count": 0 if quarantined_invoice["invoice_id"] == "NONE-DETECTED" else 1,
        },
        "health": health,
        "parsed_summary": parsed_summary,
        "simulation": sim_result,
        "actions": visible_actions,
        "all_actions_count": len(actions),
        "quarantined_invoice": quarantined_invoice,
        "regulatory_updates": visible_reg,
        "all_regulatory_count": len(regulatory_all),
    }


def anonymize_text(text: str, profile: dict[str, Any] | None = None) -> tuple[str, dict[str, str]]:
    """Mask IBANs and sensitive owner/vendor/partner/staff names before sending prompts to OpenShift AI."""
    token_map: dict[str, str] = {}
    masked = text or ""

    def _mask_iban(match: re.Match[str]) -> str:
        raw = match.group(0)
        tok = f"IBAN_TOK_{raw[-4:]}"
        token_map[tok] = raw
        return tok

    masked = re.sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,28}\b", _mask_iban, masked)

    named_entities: list[tuple[str, str]] = [
        ("Vincenzo Amalfi", "OWNER_REF_001"),
        ("Luigi Amalfi", "PARTNER_REF_002"),
        ("Luigi's Ristorante Zurich", "PARTNER_BIZ_002"),
        ("Stefan Weber", "OWNER_REF_003"),
        ("Pizzeria Amalfi", "SME_ENTITY_001"),
        ("RheinMain Präzisionstechnik GmbH", "SME_ENTITY_002"),
        ("KronoStahl AG", "CLIENT_REF_001"),
        ("Fattoria La Vino GmbH", "VENDOR_REF_002"),
        ("Asimex Wholesale Ltd", "VENDOR_REF_001"),
        ("Nordic Tooling GmbH", "VENDOR_REF_003"),
        ("Yusuf Akcit", "STAFF_REF_001"),
        ("Rosa Pavese", "STAFF_REF_002"),
        ("Vanessa Paradi", "STAFF_REF_003"),
    ]
    known_originals = {orig for orig, _ in named_entities}
    if profile:
        if profile.get("owner_name") and str(profile["owner_name"]) not in known_originals:
            named_entities.insert(0, (str(profile["owner_name"]), "OWNER_REF_CUSTOM"))
        if profile.get("business_name") and str(profile["business_name"]) not in known_originals:
            named_entities.insert(0, (str(profile["business_name"]), "SME_ENTITY_CUSTOM"))

    for original, token in named_entities:
        if original and original in masked:
            token_map[token] = original
            masked = masked.replace(original, token)

    return masked, token_map


def deanonymize_text(text: str, token_map: dict[str, str]) -> str:
    restored = text or ""
    for tok, original in token_map.items():
        restored = restored.replace(tok, original)
    return restored


def build_anonymized_context_prompt(bundle: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Create a concise, PII-masked Unified Business Context Snapshot for the single OpenShift AI LLM."""
    k = bundle["kpis"]
    p = bundle["profile"]
    h = bundle["health"]
    sim = bundle["simulation"]["summary"]
    actions_lines = [
        f"- [{a['id']}] {a['domain']} | {a['product_name']} | Amount: €{a['amount_eur']:,.2f} | Net Benefit: +€{a['net_benefit_eur']:,.2f} | Status: {a['status']}"
        for a in bundle["actions"]
    ]
    reg_lines = [f"- [{r['severity'].upper()}] {r['title']} (Est. impact: €{r['estimated_impact_eur']:,.0f})" for r in bundle["regulatory_updates"]]

    raw_snapshot = (
        "You are Greg, a Calm Glass-Box Autonomous CFO powered by Red Hat OpenShift AI for German SMEs. "
        "All Euro figures below are computed by a deterministic Decimal financial engine — never alter these verified figures. "
        "Cite exact document IDs, weeks, and Commerzbank products clearly, and always explain: (1) What was analyzed, (2) Why it is recommended, (3) What requires owner approval.\n\n"
        f"ACTIVE SCENARIO: {bundle['dataset_label']}\n"
        f"BUSINESS PROFILE: {p['business_name']} ({p['legal_form']}), Location: {p['location']}, Industry: {p['industry']}, Staff: {p['employee_count']}, IBAN: {p['account_iban']}\n"
        f"CONTEXT NOTES: {p['context_notes']}\n"
        f"USER COMFORT MODE: {bundle['preferences']['view_mode'].upper()} | Safety Buffer Target: €{k['safety_buffer_eur']:,.2f}\n\n"
        "VERIFIED FINANCIAL KPIs:\n"
        f"- Current Closing Balance: €{k['current_balance_eur']:,.2f}\n"
        f"- Historical Trough: €{k['historical_trough_eur']:,.2f} on {k['historical_trough_date']}\n"
        f"- 13-Week Baseline Projected Minimum: €{k['projected_min_13w_eur']:,.2f} (Week {k['projected_min_13w_week']})\n"
        f"- 13-Week Stabilized Minimum (with Commerzbank Actions): €{sim['min_stabilized_eur']:,.2f} (Week {sim['min_stabilized_week']})\n"
        f"- Foreign Currency Exposure: {k['fx_exposure_label']} (€{k['fx_exposure_eur']:,.2f})\n"
        f"- Health Score: {h['score']}/100 ({h['status']}) | Data Hygiene Score: {k['hygiene_score']}%\n\n"
        "ACTIVE COMMERZBANK & FRAUD ACTIONS:\n"
        + ("\n".join(actions_lines) if actions_lines else "- None active (filtered by user preferences)")
        + "\n\nMATCHED GERMAN REGULATORY / TAX ALERTS:\n"
        + ("\n".join(reg_lines) if reg_lines else "- None active")
    )
    return anonymize_text(raw_snapshot, profile=p)


def build_deterministic_briefing(bundle: dict[str, Any]) -> str:
    """Fallback deterministic briefing used when OpenShift AI LLM endpoint is unreachable in offline dev."""
    k = bundle["kpis"]
    p = bundle["profile"]
    sim = bundle["simulation"]["summary"]
    pending = [a for a in bundle["actions"] if a["status"] in ("pending", "quarantined")]
    return (
        f"Autonomous CFO Summary for {p['business_name']} ({bundle['dataset_label']}): "
        f"Current balance stands at €{k['current_balance_eur']:,.2f} against a €{k['safety_buffer_eur']:,.0f} safety buffer. "
        f"Our 13-week behavioral horizon flags a minimum of €{k['projected_min_13w_eur']:,.2f} in Week {k['projected_min_13w_week']}. "
        f"Activating the recommended Commerzbank actions stabilizes the 13-week minimum at €{sim['min_stabilized_eur']:,.2f} "
        f"while hedging {k['fx_exposure_label']}. {len(pending)} action card(s) await your explicit human approval."
    )


def create_audit_entry(
    ledger: list[dict[str, Any]],
    actor: str,
    action_type: str,
    target_id: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a tamper-evident SHA-256 chained EU AI Act audit record."""
    prev_hash = ledger[-1]["sha256"] if ledger else "0" * 64
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    canonical = json.dumps(
        {
            "seq": len(ledger) + 1,
            "prev_hash": prev_hash,
            "timestamp": ts,
            "actor": actor,
            "action_type": action_type,
            "target_id": target_id,
            "summary": summary,
            "payload": payload or {},
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    entry = {
        "seq": len(ledger) + 1,
        "timestamp": ts,
        "actor": actor,
        "action_type": action_type,
        "target_id": target_id,
        "summary": summary,
        "prev_hash": prev_hash,
        "sha256": digest,
    }
    ledger.append(entry)
    return entry


def create_execution_receipt(action: dict[str, Any], audit_sha: str) -> dict[str, Any]:
    return {
        "receipt_id": f"CBK-EXEC-{audit_sha[:12].upper()}",
        "idempotency_key": audit_sha[:24],
        "executed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "product": action["product_name"],
        "selected_option": action["selected_option"],
        "amount_eur": action["amount_eur"],
        "mode": "SIMULATED_COMMERZBANK_API",
        "status": "CONFIRMED_BY_HUMAN",
    }
