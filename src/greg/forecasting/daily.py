"""Statistical daily liquidity fallback forecaster for ledgers with under 12 full months."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any


def _ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def run_daily_liquidity_forecast(
    transactions: list[dict[str, Any]],
    as_of_date: date,
    current_balance: float,
    horizon_days: int,
    account_currency: str = "EUR",
    approved_credit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project daily balances using detected recurring schedules, weekday averages, and manual items."""
    history = [t for t in transactions if t["date_obj"] <= as_of_date]
    distinct_hist_months = sorted({_ym(t["date_obj"]) for t in history})
    num_hist_months = max(1, len(distinct_hist_months))

    # Step 1: Detect recurring monthly / quarterly schedules grouped by (counterparty, raw_type).
    raw_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in history:
        raw_groups[(t["counterparty"], t.get("raw_type") or t["category"])].append(t)

    recurring_tx_ids: set[str] = set()
    recurring_schedules: list[dict[str, Any]] = []

    for (cp, _raw_tp), items in raw_groups.items():
        item_months = {_ym(i["date_obj"]) for i in items}
        if len(items) > len(item_months) * 1.35:
            continue

        sub_buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for i in items:
            bucket_key = round(i["date_obj"].day / 5) * 5 if len(items) < 10 else 0
            sub_buckets[bucket_key].append(i)

        for s_items in sub_buckets.values():
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

            month_indices = [y * 12 + m for (y, m) in s_months]
            gaps = [b - a for a, b in zip(month_indices, month_indices[1:], strict=False)]

            cadence: str | None = None
            if len(s_items) >= 2 and gaps and all(g == 1 for g in gaps):
                cadence = "monthly"
            elif len(s_items) >= 2 and sum(1 for g in gaps if g == 1) >= len(gaps) * 0.6:
                cadence = "monthly"
            elif len(s_items) >= 2 and gaps and all(g == 3 for g in gaps):
                cadence = "quarterly"
            elif num_hist_months == 1 and len(s_items) == 1 and abs(mean_amt) >= 300:
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

    # Step 2: Variable daily cash-flow baseline from recent active non-recurring months.
    var_txs = [t for t in history if t["id"] not in recurring_tx_ids]
    active_var_months = sorted({_ym(t["date_obj"]) for t in var_txs})
    recent_active_months = set(active_var_months[-2:]) if active_var_months else set()

    var_by_day_in: dict[date, float] = defaultdict(float)
    var_by_day_out: dict[date, float] = defaultdict(float)
    for t in var_txs:
        d = t["date_obj"]
        if _ym(d) in recent_active_months:
            if t["amount"] >= 0:
                var_by_day_in[d] += t["amount"]
            else:
                var_by_day_out[d] += t["amount"]

    weekday_in: dict[int, list[float]] = defaultdict(list)
    weekday_out: dict[int, list[float]] = defaultdict(list)

    first_hist_date = min(t["date_obj"] for t in history)
    d_ptr = first_hist_date
    while d_ptr <= as_of_date:
        if not recent_active_months or _ym(d_ptr) in recent_active_months:
            wd = d_ptr.weekday()
            weekday_in[wd].append(var_by_day_in.get(d_ptr, 0.0))
            weekday_out[wd].append(var_by_day_out.get(d_ptr, 0.0))
        d_ptr += timedelta(days=1)

    avg_wd_in = {wd: (statistics.mean(vals) if vals else 0.0) for wd, vals in weekday_in.items()}
    avg_wd_out = {wd: (statistics.mean(vals) if vals else 0.0) for wd, vals in weekday_out.items()}

    # Step 3: Future manual operations inside (as_of_date, as_of_date + max_sim_days].
    max_sim_days = max(horizon_days, 365)
    end_forecast_date = as_of_date + timedelta(days=horizon_days)
    end_sim_date = as_of_date + timedelta(days=max_sim_days)
    future_manual_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for t in transactions:
        if t.get("source") == "manual" and as_of_date < t["date_obj"] <= end_sim_date:
            future_manual_by_date[t["date_obj"]].append(t)

    # Step 4: Roll forward day by day up to max_sim_days (365d) so Mini-Map always has full 1Y future series.
    credit_cushion = (
        float(approved_credit["amount"])
        if approved_credit and approved_credit.get("amount", 0) > 0
        else 0.0
    )

    proj_balance = current_balance
    forecast_series: list[dict[str, Any]] = []
    full_forecast_series: list[dict[str, Any]] = []
    upcoming_bills: list[dict[str, Any]] = []
    total_expected_in = 0.0
    total_expected_out = 0.0

    lowest_balance = current_balance
    lowest_date = as_of_date.isoformat()
    first_negative_date: str | None = as_of_date.isoformat() if current_balance < 0 else None
    last_negative_date: str | None = as_of_date.isoformat() if current_balance < 0 else None
    days_below_zero = 0

    for step in range(1, max_sim_days + 1):
        f_date = as_of_date + timedelta(days=step)
        wd = f_date.weekday()
        day_in = avg_wd_in.get(wd, 0.0)
        day_out = avg_wd_out.get(wd, 0.0)
        day_events: list[dict[str, Any]] = []

        for sched in recurring_schedules:
            if f_date.day != sched["typical_day"]:
                continue
            months_diff = (f_date.year - sched["last_year"]) * 12 + (f_date.month - sched["last_month"])
            is_due = sched["cadence"] == "monthly" or (
                sched["cadence"] == "quarterly" and months_diff % 3 == 0
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
                if step <= horizon_days and amt < 0:
                    upcoming_bills.append(bill_item)

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
            if step <= horizon_days and amt < 0:
                upcoming_bills.append(m_item)

        proj_balance = round(proj_balance + day_in + day_out, 2)
        pt_obj = {
            "date": f_date.isoformat(),
            "balance": proj_balance,
            "balance_with_credit": round(proj_balance + credit_cushion, 2),
            "expected_in": round(day_in, 2),
            "expected_out": round(abs(day_out), 2),
            "events": day_events,
        }
        full_forecast_series.append(pt_obj)

        if step <= horizon_days:
            total_expected_in += day_in
            total_expected_out += abs(day_out)
            if proj_balance < lowest_balance:
                lowest_balance = proj_balance
                lowest_date = f_date.isoformat()

            if proj_balance < 0:
                days_below_zero += 1
                if first_negative_date is None:
                    first_negative_date = f_date.isoformat()
                last_negative_date = f_date.isoformat()

            forecast_series.append(pt_obj)

    goes_below_zero = lowest_balance < 0
    shortfall_amount = round(abs(lowest_balance), 2) if goes_below_zero else 0.0
    recommended_credit = (
        float(math.ceil((shortfall_amount * 1.10) / 100.0) * 100) if goes_below_zero else 0.0
    )

    recovery_date: str | None = None
    if goes_below_zero and last_negative_date:
        last_neg_dt = date.fromisoformat(last_negative_date)
        if last_neg_dt < end_forecast_date:
            recovery_date = (last_neg_dt + timedelta(days=1)).isoformat()

    cur_sym = "€" if account_currency == "EUR" else f"{account_currency} "
    analysis_summary = (
        f"Starting from {as_of_date.isoformat()} with a balance of {cur_sym}{current_balance:,.2f}, "
        f"Greg checked your past payment patterns and found {len(recurring_schedules)} regular "
        f"monthly/quarterly payments. Over the next {horizon_days} days, expected income is "
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
            f"To keep your account safely above zero without paying high unarranged bank overdraft "
            f"fees, you should request a short-term credit line of "
            f"{cur_sym}{recommended_credit:,.2f} from your bank."
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
        "full_forecast_series": full_forecast_series,
    }
