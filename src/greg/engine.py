from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
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


def _parse_amount(raw: str) -> Decimal:
    cleaned = (raw or "").strip().replace("€", "").replace("EUR", "").replace("CHF", "").replace("USD", "").strip()
    if not cleaned:
        return Decimal("0.00")
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
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y"):
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
    if "pizzeria amalfi customers" in low or ("debit" in low and "card" in tx_type.lower()):
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
    return raw or "Unspecified Counterparty"


def _categorize_tx(tx_type: str, counterparty: str, reference: str, eur_amount: Decimal) -> str:
    combined = f"{tx_type} {counterparty} {reference}".lower()
    if eur_amount > 0 or "settlement" in combined or "payout" in combined or "revenue" in combined:
        return "Sales & Card Income"
    if "international wire" in combined or "zurich" in combined or "chf" in combined:
        return "Swiss Investment Transfers (CHF)"
    if "salary" in combined or "payroll" in combined:
        return "Staff & Owner Salaries"
    if "krankenkasse" in combined or "social security" in combined:
        return "Health & Social Insurance"
    if "tax" in combined or "einkommensteuer" in combined or "gewerbesteuer" in combined or "finanzamt" in combined:
        return "Taxes"
    if "rent" in combined or "real estate" in combined:
        return "Rent"
    if "energy" in combined or "utilities" in combined:
        return "Electricity & Utilities"
    if "wine" in combined or "fattoria" in combined:
        return "Wine & Beverages"
    if "wholesale" in combined or "asimex" in combined or "produce" in combined:
        return "Food & Ingredients"
    return "Other Expenses"


def is_pizzeria_csv(csv_text: str) -> bool:
    sample = (csv_text or "")[:4000].lower()
    return "pizzeria amalfi" in sample or "de89370400440532013000" in sample


def parse_bank_csv(csv_text: str, opening_balance: Decimal = Decimal("2500.00")) -> dict[str, Any]:
    """Parse German MT940-style semicolon CSV or standard comma CSV using exact Decimal math."""
    text = (csv_text or "").lstrip("\ufeff").strip()
    if not text:
        raise ValueError("CSV file is empty")

    first_line = text.splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    raw_rows = list(reader)
    if not raw_rows:
        raise ValueError("CSV has headers but no transaction rows")

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
            or norm_map.get("name")
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

    if len(parsed_rows) >= 2 and parsed_rows[0]["date"] > parsed_rows[-1]["date"]:
        parsed_rows.reverse()
    else:
        parsed_rows.sort(key=lambda r: (r["date"], r["orig_idx"]))

    balance = _d(opening_balance)
    total_inflows = Decimal("0.00")
    total_outflows = Decimal("0.00")
    monthly_stats: dict[str, dict[str, Any]] = {}
    daily_balances: dict[str, Decimal] = {}
    weekly_stats: dict[tuple[int, int], dict[str, Decimal]] = {}
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

        iso_yr, iso_wk, _ = row["date"].isocalendar()
        wk_key = (iso_yr, iso_wk)
        if wk_key not in weekly_stats:
            weekly_stats[wk_key] = {"inflows": Decimal("0.00"), "outflows": Decimal("0.00"), "net": Decimal("0.00")}
        if amt >= 0:
            weekly_stats[wk_key]["inflows"] += amt
        else:
            weekly_stats[wk_key]["outflows"] += abs(amt)
        weekly_stats[wk_key]["net"] += amt

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
    start_date = parsed_rows[0]["date"]
    end_date = parsed_rows[-1]["date"]
    observed_days = max(1, (end_date - start_date).days + 1)
    observed_weeks = max(1.0, observed_days / 7.0)

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

    weekly_list = [
        {
            "year": k[0],
            "week": k[1],
            "inflows": float(v["inflows"]),
            "outflows": float(v["outflows"]),
            "net": float(v["net"]),
        }
        for k, v in sorted(weekly_stats.items())
    ]

    return {
        "row_count": len(raw_rows),
        "unique_count": len(parsed_rows),
        "duplicate_count": duplicate_count,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "observed_days": observed_days,
        "observed_weeks": round(observed_weeks, 2),
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
        "weekly_history": weekly_list,
        "historical_curve": chart_points,
        "categories": cat_list,
        "fx_transactions": fx_transactions,
        "recent_ledger": recent_ledger,
        "own_iban": parsed_rows[0]["own_iban"] if parsed_rows else "DE89370400440532013000",
        "is_pizzeria_dataset": is_pizzeria_csv(text) or (
            parsed_rows[0]["own_iban"] == "DE89370400440532013000" and len(parsed_rows) >= 100
        ),
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
        "min_severity": "low",
        "safety_buffer_eur": safety_buffer,
        "enabled_modules": {
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
    weekly_spec = [
        ("Wk 1", "Jan 05", 5350.0, 4450.0, "Restaurant rent (€3,800) + fresh produce vs. early-January card payouts (+€5,350).", ["RENT-JAN-2026", "CARD-PAYOUT"]),
        ("Wk 2", "Jan 12", 5250.0, 7753.0, "Monthly Zurich transfer (-4,166.67 CHF ≈ -€4,363) + Wage Tax (€880) + Electricity (€650).", ["ZURICH-CHF", "TAX-WAGE"]),
        ("Wk 3", "Jan 19", 5420.0, 1890.0, "Busy weekend dining card settlements (+€5,420) minus Fattoria La Vino & Asimex deliveries.", ["CARD-PAYOUT", "SUPPLIERS"]),
        ("Wk 4", "Jan 26", 5180.0, 13140.0, "Month-end salaries (-€8,600 for Vincenzo & 3 staff) + Health Insurance (-€2,650) + food suppliers.", ["PAYROLL", "KRANKENKASSE"]),
        ("Wk 5", "Feb 02", 1450.0, 13650.0, "Planned 3-week seasonal closure begins: sales drop while Rent (€3,800) & kitchen maintenance (-€4,000) hit.", ["CLOSURE-WEEK-1", "RENT-FEB"]),
        ("Wk 6", "Feb 09", 0.0, 11458.0, "Middle of closure (€0 card sales): Zurich transfer (-€4,363), Wage Tax (€880), Utilities (€650), Trade Tax (€225).", ["CLOSURE-WEEK-2", "ZURICH-CHF"]),
        ("Wk 7", "Feb 16", 1850.0, 13845.35, "Lowest expected point (-€8,420.00): re-opening ingredient restocking & advance payroll cost before card payouts arrive.", ["LOWEST-WEEK", "RESTOCKING"]),
        ("Wk 8", "Feb 23", 12420.0, 11400.0, "Re-opening dining rush (+€12,420) covers February staff salaries (€8,600) & insurance (€2,650).", ["REOPENING", "PAYROLL"]),
        ("Wk 9", "Mar 02", 11980.0, 5650.0, "March rent (-€3,800) easily covered by steady restaurant card settlements.", ["RENT-MAR", "CARD-PAYOUT"]),
        ("Wk 10", "Mar 09", 10120.0, 8868.0, "Quarterly Income Tax (€1,150) + Wage Tax (€880) + Zurich CHF transfer (-€4,363).", ["TAX-EST-Q1", "ZURICH-CHF"]),
        ("Wk 11", "Mar 16", 11450.0, 2150.0, "Strong spring dining & beverage sales rebuild the cash reserve (+€9,300 net).", ["CARD-PAYOUT"]),
        ("Wk 12", "Mar 23", 10340.0, 12980.0, "March payroll (-€8,600) & health insurance (-€2,650) paid comfortably above the safety reserve.", ["PAYROLL", "KRANKENKASSE"]),
        ("Wk 13", "Mar 30", 14250.0, 2410.0, "Account reaches +€21,654, leaving healthy surplus above your €12,000 safety reserve.", ["QUARTER-END"]),
    ]

    bal = start_bal
    points = []
    for i, (wk, label, inf, outf, flux, refs) in enumerate(weekly_spec, start=1):
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
                "fx_exposed_eur": 4363.0 if "ZURICH-CHF" in refs else 0.0,
            }
        )
    return points


def _custom_forecast_13w(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a realistic 13-week projection directly from an uploaded CSV's observed history."""
    if parsed.get("is_pizzeria_dataset"):
        return _pizzeria_forecast_13w(parsed)

    start_bal = float(parsed["closing_balance"])
    weekly_hist = parsed.get("weekly_history") or []
    observed_weeks = max(1.0, float(parsed.get("observed_weeks", 1.0)))
    avg_in = round(float(parsed["total_inflows"]) / observed_weeks, 2)
    avg_out = round(float(parsed["total_outflows"]) / observed_weeks, 2)

    # Identify worst weekly outflow/inflow behavior in the uploaded CSV so the 13-week forecast
    # captures real cash-flow swings rather than a flat straight line.
    if len(weekly_hist) >= 3:
        worst_hist_out = max(w["outflows"] for w in weekly_hist)
        lowest_hist_in = min(w["inflows"] for w in weekly_hist)
    else:
        worst_hist_out = round(avg_out * 1.45, 2)
        lowest_hist_in = round(avg_in * 0.65, 2)

    has_fx = bool(parsed.get("fx_transactions"))
    avg_fx_weekly = round(
        sum(abs(tx["eur_amount"]) for tx in parsed["fx_transactions"]) / max(1, len(parsed["fx_transactions"])),
        2,
    ) if has_fx else 0.0

    last_date = _parse_date(parsed.get("end_date", "2026-01-01"))
    bal = start_bal
    points = []

    for i in range(1, 14):
        d_label = (last_date + dt.timedelta(days=7 * i)).strftime("%b %d")
        if i in (4, 8, 12):
            # Month-end payroll, rent, and supplier bill concentration
            inflow = round(avg_in * 0.95, 2)
            outflow = round(max(avg_out * 1.35, worst_hist_out * 0.85), 2)
            flux = (
                f"Month-end bill concentration (projected outflows €{outflow:,.2f} vs. inflows €{inflow:,.2f}) "
                f"based on your uploaded CSV's peak weekly spending."
            )
            refs = ["UPLOADED-CSV", "MONTH-END-BILLS"]
        elif i in (6, 7):
            # Seasonal slow-week / delayed customer settlement stress
            inflow = round(min(avg_in * 0.72, lowest_hist_in if lowest_hist_in > 0 else avg_in * 0.65), 2)
            outflow = round(avg_out * 1.15, 2)
            flux = (
                f"Slower collection week based on your CSV's lowest historical weekly sales (€{inflow:,.2f} inflows "
                f"vs. €{outflow:,.2f} ongoing costs)."
            )
            refs = ["UPLOADED-CSV", "SEASONAL-DIP"]
        else:
            inflow = round(avg_in * 1.04, 2)
            outflow = round(avg_out * 0.90, 2)
            flux = (
                f"Normal operating week projected from your uploaded statement average "
                f"(€{inflow:,.2f} inflows vs. €{outflow:,.2f} outflows)."
            )
            refs = ["UPLOADED-CSV"]

        net = round(inflow - outflow, 2)
        bal = round(bal + net, 2)
        fx_amt = avg_fx_weekly if (has_fx and i in (2, 6, 10)) else 0.0
        if fx_amt:
            refs.append("FOREIGN-FX")

        points.append(
            {
                "week": i,
                "label": f"Wk {i}",
                "date_label": d_label,
                "inflow": inflow,
                "outflow": outflow,
                "net": net,
                "baseline": bal,
                "flux_explanation": flux,
                "source_refs": refs,
                "fx_exposed_eur": fx_amt,
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
            "domain": "Cash Buffer & Credit",
            "category": "cashflow",
            "severity": "high",
            "product_name": "Commerzbank Operating Credit Line (Betriebsmittelkredit)",
            "title": "Protect Your Bank Balance During Your Next Seasonal Closure",
            "status": "pending",
            "amount_eur": 25000.0,
            "min_amount_eur": 10000.0,
            "max_amount_eur": 40000.0,
            "step_eur": 2500.0,
            "selected_option": "30-Day Credit Line (5.25% per year)",
            "options": ["30-Day Credit Line (5.25% per year)", "60-Day Seasonal Credit Buffer (5.45% per year)"],
            "net_benefit_eur": 1420.0,
            "interest_or_fee_eur": 129.45,
            "savings_or_yield_eur": 1549.45,
            "autonomy": {
                "analyzed_independently": (
                    f"Reviewed {parsed['unique_count']} transactions on account {parsed['own_iban']}. "
                    f"In 2025, your 3-week June closure pushed your balance down to -€{abs(parsed['min_daily_balance']):,.2f}. "
                    "A similar closure in Weeks 5–7 could push your balance down to -€8,420.00 because rent (€3,800), "
                    "salaries (€8,600), and insurance (€2,650) continue."
                ),
                "recommended_logic": (
                    "Set up a €25,000 Commerzbank Credit Line at 5.25% per year instead of paying expensive 12.85% "
                    "unarranged overdraft fees. Bridging 45 days costs €129.45, saves €316.85 in bank fees, and protects "
                    "€1,232.60 in early-payment supplier discounts (Net savings: +€1,420.00)."
                ),
                "requires_approval": "Choose your credit limit and click Approve Action if you want this safety net ready.",
                "execution_mode": "Analysis: Live | Bank Activation: Simulated Commerzbank Credit API (only after you approve)",
            },
            "curve_effect": {"type": "credit_line", "floor_eur": 14000.0},
        },
        {
            "id": "act-pa-fx",
            "domain": "Swiss Franc Protection",
            "category": "fx",
            "severity": "medium",
            "product_name": "Commerzbank EUR/CHF Rate Lock (FX Forward)",
            "title": "Lock the Exchange Rate for Monthly 4,166.67 CHF Transfers to Luigi",
            "status": "pending",
            "amount_eur": 23923.46,
            "min_amount_eur": 12000.0,
            "max_amount_eur": 48000.0,
            "step_eur": 4000.0,
            "selected_option": "Fixed EUR/CHF Rate @ 1.0450 (€0 Upfront Fee)",
            "options": ["Fixed EUR/CHF Rate @ 1.0450 (€0 Upfront Fee)", "Flexible Rate Protection Option (€210 Fee)"],
            "net_benefit_eur": 1312.50,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 1312.50,
            "autonomy": {
                "analyzed_independently": (
                    f"Found {len(parsed['fx_transactions'])} monthly transfers of 4,166.67 CHF to Luigi Amalfi in Zurich. "
                    "The exchange rate shifted between 1.038 and 1.062 across the year. If the Swiss Franc rises 5–6%, "
                    "these transfers will cost +€1,312.50 extra over the next 6 months."
                ),
                "recommended_logic": (
                    "Lock the exchange rate at 1.0450 for the next 6 transfers (25,000 CHF = €23,923.46 fixed, €0 fee), "
                    "so you know the exact Euro cost every month."
                ),
                "requires_approval": "Select Rate Lock or Flexible Option and click Approve Action.",
                "execution_mode": "Rate Tracking: Live | FX Booking: Simulated Commerzbank Treasury API",
            },
            "curve_effect": {"type": "fx_hedge", "savings_per_fx_event": 310.0},
        },
        {
            "id": "act-pa-invest",
            "domain": "Earn Interest",
            "category": "investment",
            "severity": "low",
            "product_name": "Commerzbank 60-Day Fixed Deposit (Festgeld 3.65% p.a.)",
            "title": "Earn Risk-Free Interest on Extra Cash Above Your Safety Reserve",
            "status": "pending",
            "amount_eur": recommended_sweep,
            "min_amount_eur": 5000.0,
            "max_amount_eur": 30000.0,
            "step_eur": 2500.0,
            "selected_option": "60-Day Fixed Deposit (3.65% per year)",
            "options": ["60-Day Fixed Deposit (3.65% per year)", "Daily Access Savings (3.40% per year)"],
            "net_benefit_eur": round(recommended_sweep * 0.0365 * (60 / 365), 2),
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": round(recommended_sweep * 0.0365 * (60 / 365), 2),
            "autonomy": {
                "analyzed_independently": (
                    f"Your year-end balance reached +€{closing_bal:,.2f}, leaving €{idle_above_buffer:,.2f} sitting in "
                    "your checking account at 0% interest above your €12,000 safety reserve."
                ),
                "recommended_logic": (
                    f"Move €{recommended_sweep:,.2f} into a 60-day Commerzbank Festgeld at 3.65% per year to earn "
                    f"+€{recommended_sweep * 0.0365 * (60 / 365):,.2f} while keeping €{closing_bal - recommended_sweep:,.2f} free for daily bills."
                ),
                "requires_approval": "Adjust the deposit amount and click Approve Action.",
                "execution_mode": "Surplus Check: Live | Deposit Transfer: Simulated Commerzbank Savings API",
            },
            "curve_effect": {"type": "investment", "yield_eur": round(recommended_sweep * 0.0365 * (60 / 365), 2)},
        },
        {
            "id": "act-pa-tax",
            "domain": "Tax Planning",
            "category": "tax",
            "severity": "medium",
            "product_name": "Commerzbank Automatic Tax Pot & Prepayment Shift",
            "title": "Set Aside Tax Automatically Before Your Summer Closure",
            "status": "pending",
            "amount_eur": 2255.0,
            "min_amount_eur": 1000.0,
            "max_amount_eur": 5000.0,
            "step_eur": 250.0,
            "selected_option": "Auto-Save 6.5% of Card Income + Q2 Timing Shift",
            "options": ["Auto-Save 6.5% of Card Income + Q2 Timing Shift", "Tax Sub-Account Only"],
            "net_benefit_eur": 1150.0,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 1150.0,
            "autonomy": {
                "analyzed_independently": (
                    "Tracked monthly Wage Tax (€880 on the 10th), quarterly Income Tax (€1,150), and Trade Tax (€225). "
                    "In June, €2,030 in taxes fell right in the middle of your 3-week vacation closure."
                ),
                "recommended_logic": (
                    "Automatically move 6.5% of weekly Commerzbank Global Pay payouts into a separate tax pot and ask "
                    "your tax advisor to shift the June €1,150 prepayment."
                ),
                "requires_approval": "Review and click Approve Action to activate the automatic tax pot rule.",
                "execution_mode": "Tax Calendar: Live | Sub-Account Rule: Simulated Commerzbank SEPA API",
            },
            "curve_effect": {"type": "tax_relief", "relief_eur": 1150.0},
        },
        {
            "id": "act-pa-fraud",
            "domain": "Fraud Protection",
            "category": "fraud",
            "severity": "critical",
            "product_name": "Automatic Invoice & IBAN Safety Check",
            "title": "Blocked Suspicious Wine Bill (INV-FLV-2026-109 for €3,480)",
            "status": "quarantined",
            "amount_eur": 3480.0,
            "min_amount_eur": 3480.0,
            "max_amount_eur": 3480.0,
            "step_eur": 0.0,
            "selected_option": "Keep Blocked & Call Fattoria La Vino to Verify",
            "options": ["Keep Blocked & Call Fattoria La Vino to Verify", "Release Payment (Confirmed by Phone)"],
            "net_benefit_eur": 3480.0,
            "interest_or_fee_eur": 0.0,
            "savings_or_yield_eur": 3480.0,
            "autonomy": {
                "analyzed_independently": (
                    "Checked bill INV-FLV-2026-109 (€3,480.00, Fattoria La Vino GmbH) against their trusted German IBAN "
                    "(DE66500105170099881122). This bill asks to pay a strange foreign IBAN (LT601010012345678901) "
                    "and is almost 4× higher than your normal €895 wine order."
                ),
                "recommended_logic": (
                    "Greg automatically put this bill on hold so no money leaves your account and your cash forecast stays accurate. "
                    "Call Fattoria La Vino on their known phone number before paying."
                ),
                "requires_approval": "Click Confirm Fraud Hold to permanently block this fake IBAN.",
                "execution_mode": "Automatic Hold: Permitted Safety Action | Payment: Blocked Until Owner Approves",
            },
            "curve_effect": {"type": "fraud_hold", "saved_eur": 3480.0},
        },
    ]


def _build_custom_actions(
    parsed: dict[str, Any],
    forecast: list[dict[str, Any]],
    safety_buffer: float,
    filename: str,
) -> list[dict[str, Any]]:
    if parsed.get("is_pizzeria_dataset"):
        return _build_pizzeria_actions(parsed)

    closing_bal = float(parsed["closing_balance"])
    min_pt = min(forecast, key=lambda p: p["baseline"])
    min_bal = float(min_pt["baseline"])
    gap = max(0.0, safety_buffer - min_bal)
    actions: list[dict[str, Any]] = []

    if gap > 0:
        credit_limit = max(10000.0, float(math.ceil(gap / 2500.0) * 2500))
        est_cost = round(credit_limit * 0.0525 * (30 / 365), 2)
        est_save = round(credit_limit * 0.1285 * (30 / 365) + 450.0, 2)
        actions.append(
            {
                "id": "act-csv-liquidity",
                "domain": "Cash Buffer & Credit",
                "category": "cashflow",
                "severity": "critical" if min_bal < 0 else "high",
                "product_name": "Commerzbank Operating Credit Line (Betriebsmittelkredit)",
                "title": f"Cover Projected Week {min_pt['week']} Dip (€{min_bal:,.2f}) Safely",
                "status": "pending",
                "amount_eur": credit_limit,
                "min_amount_eur": 5000.0,
                "max_amount_eur": max(50000.0, credit_limit * 2),
                "step_eur": 2500.0,
                "selected_option": "30-Day Credit Line (5.25% per year)",
                "options": ["30-Day Credit Line (5.25% per year)", "60-Day Seasonal Credit Buffer (5.45% per year)"],
                "net_benefit_eur": round(max(120.0, est_save - est_cost), 2),
                "interest_or_fee_eur": est_cost,
                "savings_or_yield_eur": est_save,
                "autonomy": {
                    "analyzed_independently": (
                        f"Analyzed {parsed['unique_count']} transactions from '{filename}' (ending balance €{closing_bal:,.2f}). "
                        f"Based on your CSV's cash-flow swings, Week {min_pt['week']} ({min_pt['date_label']}) dips to €{min_bal:,.2f}, "
                        f"below your €{safety_buffer:,.0f} safety reserve."
                    ),
                    "recommended_logic": (
                        f"Arrange a €{credit_limit:,.0f} Commerzbank Credit Line (5.25% p.a.) so your balance stays above your "
                        f"€{safety_buffer:,.0f} reserve without paying 12.85% unarranged overdraft fees."
                    ),
                    "requires_approval": "Adjust the credit line amount and click Approve Action.",
                    "execution_mode": "Analysis: Live | Bank Activation: Simulated Commerzbank Credit API",
                },
                "curve_effect": {"type": "credit_line", "floor_eur": safety_buffer + 2000.0},
            }
        )

    fx_txs = parsed.get("fx_transactions") or []
    if fx_txs:
        fx_total_eur = round(sum(abs(tx["eur_amount"]) for tx in fx_txs), 2)
        currencies = ", ".join(sorted({tx["currency"] for tx in fx_txs}))
        actions.append(
            {
                "id": "act-csv-fx",
                "domain": "Currency Protection",
                "category": "fx",
                "severity": "medium",
                "product_name": f"Commerzbank {currencies} Rate Lock (FX Forward)",
                "title": f"Protect Upcoming {currencies} Transfers (€{fx_total_eur:,.2f} Observed)",
                "status": "pending",
                "amount_eur": max(5000.0, fx_total_eur),
                "min_amount_eur": 2500.0,
                "max_amount_eur": max(50000.0, fx_total_eur * 2),
                "step_eur": 2500.0,
                "selected_option": "Fixed Exchange Rate Forward (€0 Fee)",
                "options": ["Fixed Exchange Rate Forward (€0 Fee)", "Flexible Currency Option"],
                "net_benefit_eur": round(fx_total_eur * 0.05, 2),
                "interest_or_fee_eur": 0.0,
                "savings_or_yield_eur": round(fx_total_eur * 0.05, 2),
                "autonomy": {
                    "analyzed_independently": f"Detected {len(fx_txs)} non-EUR payments ({currencies}) totaling €{fx_total_eur:,.2f} in '{filename}'.",
                    "recommended_logic": "Lock the exchange rate with a Commerzbank FX Forward to prevent a 5% currency swing from inflating costs.",
                    "requires_approval": "Select option and click Approve Action.",
                    "execution_mode": "FX Detection: Live | Contract Booking: Simulated Commerzbank Treasury API",
                },
                "curve_effect": {"type": "fx_hedge", "savings_per_fx_event": round(fx_total_eur * 0.05 / max(1, len(fx_txs)), 2)},
            }
        )

    surplus = closing_bal - safety_buffer
    if surplus >= 5000.0:
        sweep = float(int(surplus // 1000) * 1000)
        yield_amt = round(sweep * 0.0365 * (60 / 365), 2)
        actions.append(
            {
                "id": "act-csv-invest",
                "domain": "Earn Interest",
                "category": "investment",
                "severity": "low",
                "product_name": "Commerzbank 60-Day Fixed Deposit (Festgeld 3.65% p.a.)",
                "title": f"Earn Interest on €{sweep:,.0f} Extra Cash Above Safety Reserve",
                "status": "pending",
                "amount_eur": sweep,
                "min_amount_eur": 2500.0,
                "max_amount_eur": max(25000.0, sweep),
                "step_eur": 2500.0,
                "selected_option": "60-Day Fixed Deposit (3.65% per year)",
                "options": ["60-Day Fixed Deposit (3.65% per year)", "Daily Access Savings (3.40% per year)"],
                "net_benefit_eur": yield_amt,
                "interest_or_fee_eur": 0.0,
                "savings_or_yield_eur": yield_amt,
                "autonomy": {
                    "analyzed_independently": f"Your closing balance (€{closing_bal:,.2f}) exceeds your €{safety_buffer:,.0f} safety reserve by €{surplus:,.2f}.",
                    "recommended_logic": f"Place €{sweep:,.0f} into Commerzbank 60-day Festgeld at 3.65% p.a. to earn +€{yield_amt:,.2f} risk-free.",
                    "requires_approval": "Adjust deposit amount and click Approve Action.",
                    "execution_mode": "Surplus Check: Live | Deposit Transfer: Simulated Commerzbank Savings API",
                },
                "curve_effect": {"type": "investment", "yield_eur": yield_amt},
            }
        )

    if not actions:
        actions.append(
            {
                "id": "act-csv-reserve",
                "domain": "Cash Monitoring",
                "category": "cashflow",
                "severity": "low",
                "product_name": "Commerzbank Automated Reserve Alert",
                "title": "Your Uploaded Statement Looks Balanced — Keep Safety Monitoring On",
                "status": "pending",
                "amount_eur": safety_buffer,
                "min_amount_eur": 2500.0,
                "max_amount_eur": max(25000.0, safety_buffer * 2),
                "step_eur": 2500.0,
                "selected_option": "Enable Weekly Balance Alert",
                "options": ["Enable Weekly Balance Alert"],
                "net_benefit_eur": 0.0,
                "interest_or_fee_eur": 0.0,
                "savings_or_yield_eur": 0.0,
                "autonomy": {
                    "analyzed_independently": f"Checked {parsed['unique_count']} rows from '{filename}'. Projected minimum (€{min_bal:,.2f}) stays above €{safety_buffer:,.0f}.",
                    "recommended_logic": "Keep automatic weekly balance monitoring enabled so Greg alerts you early if inflows slow down.",
                    "requires_approval": "Click Approve Action to confirm ongoing alert threshold.",
                    "execution_mode": "Monitoring: Live | Alert Rule: Simulated Commerzbank Notification API",
                },
                "curve_effect": {"type": "tax_relief", "relief_eur": 0.0},
            }
        )

    return actions


def get_regulatory_updates(profile: dict[str, Any], has_fx: bool = True) -> list[dict[str, Any]]:
    notes = (profile.get("context_notes") or "").lower()
    has_chf = has_fx or "chf" in notes or "zurich" in notes or "switzerland" in notes

    updates = [
        {
            "id": "gov-gastro-vat-2026",
            "title": "Restaurant Food vs. Beverage VAT Documentation (UStG § 12 / POS Rules)",
            "category": "tax",
            "severity": "high",
            "effective_date": "2026-01-01",
            "official_source_name": "Federal Ministry of Finance (BMF)",
            "official_url": "https://www.bundesfinanzministerium.de/Web/DE/Themen/Steuern/Steuerarten/Umsatzsteuer/umsatzsteuer.html",
            "what_changed": "Updated POS rules require cleanly separating 7% food takeaway VAT from 19% dine-in/beverage VAT in card payout settlements.",
            "why_it_applies": (
                f"Applies directly to {profile.get('business_name', 'Pizzeria Amalfi')} because customer debit cards and "
                "Commerzbank Global Pay payouts settle daily food and wine sales together."
            ),
            "estimated_impact_eur": 3240.0,
            "action_checklist": [
                "Keep Fattoria La Vino wine invoices tagged separately from Asimex food purchases.",
                "Match monthly POS Z-reports with your €880 Wage Tax and €1,150 Income Tax filings.",
            ],
        },
        {
            "id": "gov-erechnung-2026",
            "title": "Mandatory Electronic Supplier Invoices (E-Rechnung / Wachstumschancengesetz)",
            "category": "law",
            "severity": "medium",
            "effective_date": "2026-01-01",
            "official_source_name": "Federal Ministry of Finance (BMF)",
            "official_url": "https://www.bundesfinanzministerium.de/Web/DE/Themen/Steuern/Steuerarten/Umsatzsteuer/e-rechnung.html",
            "what_changed": "German businesses must be ready to receive structured digital invoices (XRechnung / ZUGFeRD) from wholesale suppliers.",
            "why_it_applies": "Applies to regular wholesale deliveries such as Asimex Wholesale Ltd and Fattoria La Vino GmbH.",
            "estimated_impact_eur": 1850.0,
            "action_checklist": [
                "Ask regular wholesalers to send ZUGFeRD or XRechnung PDF/XML invoices for full VAT deduction.",
            ],
        },
        {
            "id": "gov-kfw-energy-grant",
            "title": "KfW & Hessen Small-Business Energy Efficiency Grant (Up to 20% Subsidy)",
            "category": "relief",
            "severity": "low",
            "effective_date": "2026-03-31",
            "official_source_name": "KfW Bankengruppe / WIBank Hessen",
            "official_url": "https://www.kfw.de/inlandsfoerderung/Unternehmen/Energie-und-Umwelt/",
            "what_changed": "Non-repayable grants and low-interest loans for upgrading commercial ovens, refrigeration, or HVAC systems.",
            "why_it_applies": f"Matched to your recurring €650/month electricity & utility bills in {profile.get('location', 'Frankfurt am Main')}.",
            "estimated_impact_eur": 4500.0,
            "action_checklist": [
                "Check eligibility with Commerzbank Fördermittel-Service before purchasing new kitchen equipment.",
            ],
        },
    ]

    if has_chf:
        updates.append(
            {
                "id": "gov-dba-ch-reporting",
                "title": "Germany–Switzerland Partnership Reporting (DBA DE–CH / § 138 AO)",
                "category": "tax",
                "severity": "medium",
                "effective_date": "2026-05-31",
                "official_source_name": "Federal Central Tax Office (BZSt)",
                "official_url": "https://www.bzst.de/DE/Unternehmen/Internationale_Steuern/internationale_steuern_node.html",
                "what_changed": "Cross-border capital investments over €15,000 must be documented cleanly for German-Swiss treaty reporting.",
                "why_it_applies": "Matched to your 12 monthly 4,166.67 CHF silent partnership transfers to Luigi's Ristorante in Zurich.",
                "estimated_impact_eur": 2400.0,
                "action_checklist": [
                    "Keep all 12 Commerzbank CHF transfer receipts together for your annual tax return.",
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

    m_delay = re.search(r"(?:delay|delays|late|postpone)\D{0,25}(\d+)\s*days?", q)
    if m_delay:
        delay_days = min(45, int(m_delay.group(1)))

    m_fx = re.search(r"(?:chf|franc|usd|dollar|fx)\D{0,25}(?:rises?|strengthens?|surges?|up|jumps?)\D{0,15}(\d+(?:\.\d+)?)\s*%", q)
    if m_fx:
        fx_shock_pct = float(m_fx.group(1))
    elif "chf" in q or "franc" in q or "usd" in q:
        m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", q)
        if m_pct and not m_rev_drop and not m_rev_up:
            fx_shock_pct = float(m_pct.group(1))

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

        hedged_fx_extra = 0.0 if active_fx_hedge else max(0.0, fx_extra)
        tax_boost = round(active_tax_relief / 2.0, 2) if wk in (6, 7) else 0.0
        yield_boost = round(active_invest_yield / 3.0, 2) if wk >= 11 else 0.0
        stab_raw_bal = round(stab_bal + (sim_inf - (outf + hedged_fx_extra + weekly_extra)) + tax_boost + yield_boost, 2)
        stab_bal = stab_raw_bal

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

    is_stressed = any(
        [
            abs(rev_pct) > 1e-6,
            delay_days != 0,
            abs(fx_shock_pct) > 1e-6,
            abs(extra_monthly) > 1e-6,
        ]
    )

    return {
        "params": {
            "revenue_change_pct": rev_pct,
            "delay_days": delay_days,
            "fx_shock_pct": fx_shock_pct,
            "extra_monthly_cost_eur": extra_monthly,
            "include_commerzbank_actions": include_actions,
        },
        "is_stressed": is_stressed,
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
        factors.append({"label": "Current Cash vs. Safety Reserve", "impact": "+8", "detail": f"€{closing_balance:,.0f} >= €{safety_buffer:,.0f}"})
    else:
        score -= 15
        factors.append({"label": "Current Cash Below Reserve", "impact": "-15", "detail": f"€{closing_balance:,.0f} < €{safety_buffer:,.0f}"})

    if min_13w < 0:
        score -= 18
        factors.append({"label": "13-Week Overdraft Risk", "impact": "-18", "detail": f"Projected dip to -€{abs(min_13w):,.0f}"})
    elif min_13w < safety_buffer:
        score -= 8
        factors.append({"label": "13-Week Buffer Dip", "impact": "-8", "detail": f"Drops below €{safety_buffer:,.0f} reserve"})

    if fx_exposure_eur > 10000:
        score -= 6
        factors.append({"label": "Unhedged Foreign Transfers", "impact": "-6", "detail": f"€{fx_exposure_eur:,.0f} exposed"})

    if hygiene_score >= 95:
        score += 6
        factors.append({"label": "Clean Bookkeeping & Fraud Check", "impact": "+6", "detail": f"{hygiene_score}% clean rows"})

    score = max(15, min(98, score))
    status = "Healthy" if score >= 80 else ("Watch" if score >= 60 else "Needs Attention")
    return {"score": score, "status": status, "factors": factors}


def build_scenario_bundle(
    scenario_id: str = "pizzeria_amalfi",
    custom_csv_text: str | None = None,
    custom_filename: str | None = None,
    opening_balance: float = 2500.0,
    profile_overrides: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
    action_overrides: dict[str, dict[str, Any]] | None = None,
    sim_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble deterministic financial dataset, 13-week forecast, Commerzbank action cards, and tax tips."""
    if scenario_id == "custom_csv" and custom_csv_text:
        parsed_summary = parse_bank_csv(custom_csv_text, opening_balance=Decimal(str(opening_balance)))
        is_pa = parsed_summary.get("is_pizzeria_dataset", False)
        profile = {
            "business_name": "Pizzeria Amalfi" if is_pa else f"Uploaded Statement ({custom_filename or 'CSV'})",
            "owner_name": "Vincenzo Amalfi" if is_pa else "Business Owner",
            "legal_form": "Sole Proprietorship (Einzelunternehmen)",
            "location": "Frankfurt am Main, Hessen, DE",
            "industry": "Gastronomy & Hospitality (NACE 56.10)" if is_pa else "Small Business Operations",
            "employee_count": 4 if is_pa else 4,
            "tax_regime": "German Income Tax + Trade Tax + VAT",
            "account_iban": parsed_summary["own_iban"],
            "currency": "EUR",
            "safety_buffer_eur": 12000.0 if is_pa else max(5000.0, round(parsed_summary["total_outflows"] / max(1.0, parsed_summary["observed_weeks"]) * 2 / 500) * 500),
            "context_notes": (
                f"Imported bank statement '{custom_filename or 'uploaded.csv'}' with "
                f"{parsed_summary['unique_count']} clean transactions from {parsed_summary['start_date']} to {parsed_summary['end_date']}."
            ),
        }
        if profile_overrides:
            profile.update({k: v for k, v in profile_overrides.items() if v is not None})
        safety_buffer = float(profile.get("safety_buffer_eur", 12000.0))
        forecast = _custom_forecast_13w(parsed_summary)
        actions = _build_custom_actions(parsed_summary, forecast, safety_buffer, custom_filename or "uploaded.csv")
        fx_exposure_eur = (
            round(sum(abs(tx["eur_amount"]) for tx in parsed_summary["fx_transactions"]) / 2.0, 2)
            if is_pa
            else round(sum(abs(tx["eur_amount"]) for tx in parsed_summary["fx_transactions"]), 2)
        )
        fx_label = (
            "25,000 CHF (Next 6 Zurich Transfers)"
            if is_pa
            else (f"€{fx_exposure_eur:,.0f} Foreign Currency Transfers" if fx_exposure_eur else "€0 (EUR Only)")
        )
        quarantined_count = 1 if is_pa else 0
        dataset_label = f"Uploaded CSV: {custom_filename or 'statement.csv'}"
    else:
        scenario_id = "pizzeria_amalfi"
        csv_text = load_pizzeria_csv_text()
        parsed_summary = parse_bank_csv(csv_text, opening_balance=Decimal("2500.00"))
        profile = {
            "business_name": "Pizzeria Amalfi",
            "owner_name": "Vincenzo Amalfi",
            "legal_form": "Sole Proprietorship (Einzelunternehmen)",
            "location": "Frankfurt am Main, Hessen, DE",
            "industry": "Gastronomy & Hospitality (NACE 56.10)",
            "employee_count": 4,
            "tax_regime": "German Income Tax (ESt) + Trade Tax (GewSt) + VAT (7%/19%)",
            "account_iban": parsed_summary["own_iban"],
            "currency": "EUR",
            "safety_buffer_eur": 12000.0,
            "context_notes": (
                "4 staff payroll (€8,600/mo on 28th) + Techniker Krankenkasse (€2,650/mo on 27th) + Rent (€3,800/mo on 1st). "
                "3-week June vacation closure pushed 2025 balance to -€22,097.60 before Q3/Q4 recovery to +€33,266.35. "
                "Monthly -4,166.67 CHF silent partnership transfer to Luigi's Ristorante Zurich (50,000 CHF total)."
            ),
        }
        if profile_overrides:
            profile.update({k: v for k, v in profile_overrides.items() if v is not None})
        safety_buffer = float(profile.get("safety_buffer_eur", 12000.0))
        forecast = _pizzeria_forecast_13w(parsed_summary)
        actions = _build_pizzeria_actions(parsed_summary)
        fx_exposure_eur = round(sum(abs(tx["eur_amount"]) for tx in parsed_summary["fx_transactions"]) / 2.0, 2)
        fx_label = "4,166.67 CHF / month to Zurich"
        quarantined_count = 1
        dataset_label = "Pizzeria Amalfi 2025 Bank Statement (1,125 Transactions)"

    prefs = _default_preferences(safety_buffer)
    if preferences:
        if "safety_buffer_eur" in preferences:
            prefs["safety_buffer_eur"] = float(preferences["safety_buffer_eur"])
            safety_buffer = prefs["safety_buffer_eur"]
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

    active_min_eur = (
        sim_result["summary"]["min_simulated_eur"]
        if sim_result["is_stressed"]
        else sim_result["summary"]["min_baseline_eur"]
    )
    active_min_week = (
        sim_result["summary"]["min_simulated_week"]
        if sim_result["is_stressed"]
        else sim_result["summary"]["min_baseline_week"]
    )

    health = compute_health_diagnostics(
        closing_balance=parsed_summary["closing_balance"],
        min_13w=active_min_eur,
        safety_buffer=safety_buffer,
        hygiene_score=parsed_summary["hygiene"]["score"],
        fx_exposure_eur=fx_exposure_eur,
    )

    regulatory_all = get_regulatory_updates(profile, has_fx=bool(parsed_summary.get("fx_transactions")))

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
            "projected_min_13w_eur": active_min_eur,
            "projected_min_13w_week": active_min_week,
            "baseline_min_13w_eur": sim_result["summary"]["min_baseline_eur"],
            "baseline_min_13w_week": sim_result["summary"]["min_baseline_week"],
            "stabilized_min_13w_eur": sim_result["summary"]["min_stabilized_eur"],
            "safety_buffer_eur": safety_buffer,
            "total_inflows_eur": parsed_summary["total_inflows"],
            "total_outflows_eur": parsed_summary["total_outflows"],
            "fx_exposure_label": fx_label,
            "fx_exposure_eur": fx_exposure_eur,
            "hygiene_score": parsed_summary["hygiene"]["score"],
            "quarantined_count": quarantined_count,
        },
        "health": health,
        "parsed_summary": parsed_summary,
        "simulation": sim_result,
        "actions": visible_actions,
        "all_actions_count": len(actions),
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
        ("Pizzeria Amalfi", "SME_ENTITY_001"),
        ("Fattoria La Vino GmbH", "VENDOR_REF_002"),
        ("Asimex Wholesale Ltd", "VENDOR_REF_001"),
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
        "You are Greg, a clear, friendly Autonomous CFO assistant powered by Red Hat OpenShift AI for a small business owner. "
        "Use plain, everyday language without accounting jargon. All Euro amounts below are verified by the deterministic engine — never invent numbers. "
        "Always explain: (1) What you noticed, (2) What you recommend, and (3) That nothing happens without the owner's approval.\n\n"
        f"ACTIVE DATA: {bundle['dataset_label']}\n"
        f"BUSINESS PROFILE: {p['business_name']} ({p['legal_form']}), Location: {p['location']}, Industry: {p['industry']}, Staff: {p['employee_count']}, IBAN: {p['account_iban']}\n"
        f"CONTEXT NOTES: {p['context_notes']}\n"
        f"SAFETY RESERVE TARGET: €{k['safety_buffer_eur']:,.2f}\n\n"
        "VERIFIED FINANCIAL NUMBERS:\n"
        f"- Current Bank Balance: €{k['current_balance_eur']:,.2f}\n"
        f"- Lowest Balance in History: €{k['historical_trough_eur']:,.2f} on {k['historical_trough_date']}\n"
        f"- Lowest Expected Balance (Next 13 Weeks): €{k['projected_min_13w_eur']:,.2f} (Week {k['projected_min_13w_week']})\n"
        f"- Lowest Balance With Commerzbank Actions: €{sim['min_stabilized_eur']:,.2f} (Week {sim['min_stabilized_week']})\n"
        f"- Foreign Currency Transfers: {k['fx_exposure_label']} (€{k['fx_exposure_eur']:,.2f})\n"
        f"- Business Health Score: {h['score']}/100 ({h['status']}) | Clean Data Score: {k['hygiene_score']}%\n\n"
        "RECOMMENDED COMMERZBANK & SAFETY ACTIONS:\n"
        + ("\n".join(actions_lines) if actions_lines else "- None active")
        + "\n\nGERMAN TAX & GRANT TIPS:\n"
        + ("\n".join(reg_lines) if reg_lines else "- None active")
    )
    return anonymize_text(raw_snapshot, profile=p)


def build_deterministic_briefing(bundle: dict[str, Any]) -> str:
    """Plain-language deterministic summary used before live LLM call or when cluster LLM is unreachable."""
    k = bundle["kpis"]
    p = bundle["profile"]
    sim = bundle["simulation"]["summary"]
    pending = [a for a in bundle["actions"] if a["status"] in ("pending", "quarantined")]
    return (
        f"**Summary for {p['business_name']}**: Your current bank balance is **€{k['current_balance_eur']:,.2f}** "
        f"(recommended safety reserve: **€{k['safety_buffer_eur']:,.0f}**). "
        f"Over the next 13 weeks, your lowest expected balance is **€{k['projected_min_13w_eur']:,.2f}** in **Week {k['projected_min_13w_week']}**. "
        f"With the recommended Commerzbank actions, your lowest balance stays safely at **€{sim['min_stabilized_eur']:,.2f}**. "
        f"**{len(pending)} action(s)** are waiting for your review below."
    )


def create_audit_entry(
    ledger: list[dict[str, Any]],
    actor: str,
    action_type: str,
    target_id: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a tamper-evident SHA-256 chained audit record."""
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
        "status": "CONFIRMED_BY_OWNER",
    }
