"""Forecast routing orchestrator: seasonal monthly planner (>= 12m) with daily fallback."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any

from greg.forecasting.daily import run_daily_liquidity_forecast
from greg.forecasting.seasonal import get_planner, monthly_flows


def _ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def run_forecast(
    transactions: list[dict[str, Any]],
    as_of_date: date,
    current_balance: float,
    horizon_days: int,
    horizon_months: int | None,
    month_end_balance_map: dict[str, float],
    account_currency: str = "EUR",
    approved_credit: dict[str, Any] | None = None,
    planner_cache: dict[tuple[Any, ...], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select the seasonal monthly planner when >= 12 full months exist; otherwise fall back to daily."""
    history = [t for t in transactions if t["date_obj"] <= as_of_date]

    try:
        planner = get_planner()
        cache_key = (
            as_of_date.isoformat(),
            id(planner),
            tuple((t["id"], t["date"], t["amount"], t["category"], t["source"]) for t in history),
        )
        plan = planner_cache.get(cache_key) if planner_cache is not None else None
        if plan is None:
            flows = monthly_flows(history, include_manual=True)
            if flows is None:
                plan = {"available": False, "reason": "No transactions to plan from."}
            else:
                plan = planner.plan(flows, method="auto", start_balance=0.0)
            if planner_cache is not None:
                planner_cache[cache_key] = plan
    except ImportError as exc:
        plan = {
            "available": False,
            "reason": f"Planner libraries missing ({exc.name}); using daily fallback.",
        }

    if plan.get("available"):
        last_hist_month = str(plan["last_history_month"])
        aligned_start_bal = month_end_balance_map.get(last_hist_month, current_balance)
        n_months = max(0, min(12, int(horizon_months if horizon_months is not None else 12)))

        inflow_s = plan["series"]["inflow"]
        outflow_s = plan["series"]["outflow"]
        all_plan_months = list(plan["months"])[:12]
        months = all_plan_months[:n_months]

        manual_in_by_m: dict[str, float] = defaultdict(float)
        manual_out_by_m: dict[str, float] = defaultdict(float)
        for t in transactions:
            if t.get("source") == "manual":
                mk = _ym(t["date_obj"])
                if mk > last_hist_month and mk in all_plan_months:
                    if t["amount"] >= 0:
                        manual_in_by_m[mk] += float(t["amount"])
                    else:
                        manual_out_by_m[mk] += abs(float(t["amount"]))

        credit_cushion = (
            float(approved_credit["amount"])
            if approved_credit and approved_credit.get("amount", 0) > 0
            else 0.0
        )

        bal = aligned_start_bal
        bal_pess = aligned_start_bal
        total_expected_in = 0.0
        total_expected_out = 0.0
        lowest_balance = aligned_start_bal
        lowest_date = last_hist_month
        lowest_pess = aligned_start_bal
        lowest_pess_date = last_hist_month
        first_negative_date: str | None = last_hist_month if aligned_start_bal < 0 else None
        last_negative_date: str | None = last_hist_month if aligned_start_bal < 0 else None
        months_below_zero = 0
        forecast_series: list[dict[str, Any]] = []
        full_forecast_series: list[dict[str, Any]] = []

        for idx, ym in enumerate(all_plan_months):
            add_in = manual_in_by_m.get(ym, 0.0)
            add_out = manual_out_by_m.get(ym, 0.0)
            m_in = float(inflow_s["plan"][idx]) + add_in
            m_in_lo = float(inflow_s["low"][idx]) + add_in
            m_in_hi = float(inflow_s["high"][idx]) + add_in
            m_out = float(outflow_s["plan"][idx]) + add_out
            m_out_lo = float(outflow_s["low"][idx]) + add_out
            m_out_hi = float(outflow_s["high"][idx]) + add_out

            bal = round(bal + (m_in - m_out), 2)
            bal_pess = round(bal_pess + (m_in_lo - m_out_hi), 2)

            pt_obj = {
                "date": ym,
                "balance": bal,
                "balance_pessimistic": bal_pess,
                "balance_with_credit": round(bal + credit_cushion, 2),
                "expected_in": round(m_in, 2),
                "expected_out": round(m_out, 2),
                "expected_in_low": round(m_in_lo, 2),
                "expected_in_high": round(m_in_hi, 2),
                "expected_out_low": round(m_out_lo, 2),
                "expected_out_high": round(m_out_hi, 2),
                "events": [],
            }
            full_forecast_series.append(pt_obj)

            if idx < n_months:
                total_expected_in += m_in
                total_expected_out += m_out
                if bal < lowest_balance:
                    lowest_balance = bal
                    lowest_date = ym
                if bal_pess < lowest_pess:
                    lowest_pess = bal_pess
                    lowest_pess_date = ym

                if bal < 0:
                    months_below_zero += 1
                    if first_negative_date is None:
                        first_negative_date = ym
                    last_negative_date = ym

                forecast_series.append(pt_obj)

        goes_below_zero = lowest_balance < 0
        shortfall_amount = round(abs(lowest_balance), 2) if goes_below_zero else 0.0
        recommended_credit = (
            float(math.ceil((shortfall_amount * 1.10) / 100.0) * 100) if goes_below_zero else 0.0
        )

        recovery_date: str | None = None
        if goes_below_zero and last_negative_date and months and last_negative_date < months[-1]:
            ly, lm = (int(x) for x in last_negative_date.split("-"))
            recovery_date = f"{ly + 1:04d}-01" if lm == 12 else f"{ly:04d}-{lm + 1:02d}"

        cur_sym = "€" if account_currency == "EUR" else f"{account_currency} "
        analysis_summary = (
            f"Starting after {last_hist_month} with a balance of {cur_sym}{aligned_start_bal:,.2f}, "
            f"Greg ran the seasonal monthly planner for the next {n_months} months "
            f"(Inflow: {inflow_s['method_label']}; Outflow: {outflow_s['method_label']}). "
            f"Expected total income is {cur_sym}{total_expected_in:,.2f} and expected expenses are "
            f"{cur_sym}{total_expected_out:,.2f}."
        )
        if goes_below_zero:
            rec_text = (
                f" recovers above zero in {recovery_date}"
                if recovery_date
                else f" stays below zero through {months[-1]}"
            )
            recommendation_summary = (
                f"Warning: Your expected balance drops below zero in {first_negative_date}, "
                f"reaching {cur_sym}{lowest_balance:,.2f} in {lowest_date} "
                f"({months_below_zero} month(s) below zero; it{rec_text}). "
                f"In a pessimistic stress test, it could reach {cur_sym}{lowest_pess:,.2f} "
                f"({lowest_pess_date})."
            )
        else:
            pess_note = (
                f" (Pessimistic stress test lowest: {cur_sym}{lowest_pess:,.2f} in {lowest_pess_date}.)"
            )
            recommendation_summary = (
                f"Good news: Your expected balance stays above zero for all next {n_months} months, "
                f"with a lowest expected balance of {cur_sym}{lowest_balance:,.2f} in {lowest_date}."
                + pess_note
            )

        return {
            "engine": "greg_planner",
            "granularity": "month",
            "horizon_days": horizon_days,
            "horizon_months": n_months,
            "as_of_date": as_of_date.isoformat(),
            "last_history_month": last_hist_month,
            "current_balance": aligned_start_bal,
            "total_expected_in": round(total_expected_in, 2),
            "total_expected_out": round(total_expected_out, 2),
            "detected_regular_schedules_count": 0,
            "upcoming_bills": [],
            "lowest_predicted_balance": lowest_balance,
            "lowest_predicted_date": lowest_date,
            "lowest_pessimistic_balance": lowest_pess,
            "lowest_pessimistic_date": lowest_pess_date,
            "goes_below_zero": goes_below_zero,
            "first_negative_date": first_negative_date,
            "recovery_date": recovery_date,
            "days_below_zero": 0,
            "months_below_zero": months_below_zero,
            "shortfall_amount": shortfall_amount,
            "recommended_credit_amount": recommended_credit,
            "fallback_reason": None,
            "planner_meta": {
                "inflow_method": inflow_s["method"],
                "inflow_method_label": inflow_s["method_label"],
                "outflow_method": outflow_s["method"],
                "outflow_method_label": outflow_s["method_label"],
                "inflow_classic_growth": inflow_s["classic_growth"],
                "outflow_classic_growth": outflow_s["classic_growth"],
                "flagged_inflow_months": inflow_s["flagged_months"],
                "flagged_outflow_months": outflow_s["flagged_months"],
                "network_loaded": bool(plan.get("network_loaded")),
                "notes": list(plan.get("notes") or []),
            },
            "analysis_summary": analysis_summary,
            "recommendation_summary": recommendation_summary,
            "forecast_series": forecast_series,
            "full_forecast_series": full_forecast_series,
        }

    fallback_reason = str(plan.get("reason") or "Fewer than 12 full months of history.")
    daily_res = run_daily_liquidity_forecast(
        transactions=transactions,
        as_of_date=as_of_date,
        current_balance=current_balance,
        horizon_days=horizon_days,
        account_currency=account_currency,
        approved_credit=approved_credit,
    )
    daily_res.update(
        {
            "engine": "daily_fallback",
            "granularity": "day",
            "horizon_months": None,
            "last_history_month": None,
            "months_below_zero": 0,
            "lowest_pessimistic_balance": None,
            "lowest_pessimistic_date": None,
            "fallback_reason": fallback_reason,
            "planner_meta": None,
        }
    )
    return daily_res
