"""Unit tests for `LedgerStore`, daily fallback vs monthly alignment, and FX sample ledger."""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from greg.domain.ledger import LedgerStore
from greg.llm.prompts import build_context_system_prompt
from tests.helpers import make_store_from_txs, synthetic_ledger

SAMPLE_CSV_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "requirements"
    / "red-hat-challenge-samle data"
    / "pizzeria-amalfi-bank-statement-2025-v6.csv"
)


class LedgerStoreTest(unittest.TestCase):
    def test_starts_completely_empty(self) -> None:
        store = LedgerStore()
        view = store.get_view()
        self.assertFalse(view["has_data"])
        self.assertEqual(view["months"], [])

    def test_generic_csv_and_manual_operations_zero_hardcoding(self) -> None:
        csv_text = (
            "Date,Type,Counterparty,Description,Amount,Currency\n"
            "2026-01-01,Rent,Landlord Alpha,Office Rent,-2000.00,EUR\n"
            "2026-01-10,Tax,City Tax Office,Wage Tax,-500.00,EUR\n"
            "2026-01-15,Sales,Client One,Invoice 101,3200.00,EUR\n"
            "2026-01-28,Salary,Alice Smith,Jan Salary,-1500.00,EUR\n"
            "2026-02-01,Rent,Landlord Alpha,Office Rent,-2000.00,EUR\n"
            "2026-02-10,Tax,City Tax Office,Wage Tax,-500.00,EUR\n"
            "2026-02-15,Sales,Client One,Invoice 102,1800.00,EUR\n"
            "2026-02-28,Salary,Alice Smith,Feb Salary,-1500.00,EUR\n"
        )
        store = LedgerStore()
        store.import_csv(csv_text, filename="custom.csv", opening_balance=1000.0)

        jan = store.get_view(selected_month="2026-01", horizon_days=30)
        self.assertTrue(jan["has_data"])
        self.assertEqual(jan["month_summary"]["total_income"], 3200.0)
        self.assertEqual(jan["month_summary"]["total_expenses"], 4000.0)
        self.assertEqual(jan["month_summary"]["profit"], -800.0)
        self.assertEqual(jan["month_summary"]["end_balance"], 200.0)

        cat_totals = {c["category"]: c["total"] for c in jan["expense_breakdown"]}
        self.assertEqual(cat_totals, {"Other": 2000.0, "Salaries": 1500.0, "Taxes": 500.0})

        exp_people = {
            p["name"]: p["total"]
            for cat in jan["expense_breakdown"]
            for p in cat["people"]
        }
        self.assertEqual(exp_people["Landlord Alpha"], 2000.0)
        self.assertEqual(exp_people["Alice Smith"], 1500.0)
        self.assertEqual(exp_people["City Tax Office"], 500.0)

        op = store.add_manual_operation(
            date_str="2026-03-05",
            kind="expense",
            amount=2500.0,
            counterparty="Equipment Vendor",
            category="Other",
            description="New server",
        )
        feb = store.get_view(selected_month="2026-02", horizon_days=30)
        la = feb["liquidity_agent"]
        self.assertTrue(la["goes_below_zero"])
        self.assertGreater(la["recommended_credit_amount"], 0.0)

        store.approve_credit(
            amount=la["recommended_credit_amount"], month="2026-02", horizon_days=30
        )
        feb_after = store.get_view(selected_month="2026-02", horizon_days=30)
        for pt in feb_after["liquidity_agent"]["forecast_series"]:
            self.assertAlmostEqual(
                pt["balance_with_credit"] - pt["balance"],
                la["recommended_credit_amount"],
                places=2,
            )

        self.assertTrue(store.delete_operation(op["id"]))

    def test_short_history_falls_back_to_daily_forecaster_in_ledger_store(self) -> None:
        txs = synthetic_ledger(date(2025, 7, 1), date(2025, 12, 31))
        store = make_store_from_txs(txs, opening_balance=8000.0)
        view = store.get_view(horizon_days=30)
        la = view["liquidity_agent"]
        self.assertEqual(la["engine"], "daily_fallback")
        self.assertEqual(la["granularity"], "day")
        self.assertIsNone(la["horizon_months"])
        self.assertEqual(len(la["forecast_series"]), 30)
        self.assertEqual(len(la["forecast_series"][0]["date"]), 10)
        self.assertTrue(view["monthly_history_series"])

    def test_partial_trailing_month_alignment_and_manual_future_overlay(self) -> None:
        txs = synthetic_ledger(date(2025, 1, 1), date(2026, 2, 10))
        store = make_store_from_txs(txs, opening_balance=10000.0)

        base_view = store.get_view(selected_month="2026-02", horizon_months=6)
        la = base_view["liquidity_agent"]
        self.assertEqual(la["engine"], "greg_planner")
        self.assertEqual(la["granularity"], "month")
        self.assertEqual(la["horizon_months"], 6)
        self.assertEqual(la["last_history_month"], "2026-01")
        self.assertEqual(len(la["forecast_series"]), 6)
        self.assertEqual(la["forecast_series"][0]["date"], "2026-02")
        self.assertEqual(base_view["monthly_history_series"][-1]["date"], "2026-01")
        self.assertEqual(base_view["monthly_history_series"][-1]["balance"], la["current_balance"])

        store.add_manual_operation("2026-04-15", "expense", 4000.0, "One-off Equipment", "Other")
        after_view = store.get_view(selected_month="2026-02", horizon_months=6)
        apr_base = [
            pt for pt in base_view["liquidity_agent"]["forecast_series"] if pt["date"] == "2026-04"
        ][0]
        apr_after = [
            pt for pt in after_view["liquidity_agent"]["forecast_series"] if pt["date"] == "2026-04"
        ][0]
        self.assertAlmostEqual(apr_after["expected_out"] - apr_base["expected_out"], 4000.0, places=2)
        self.assertAlmostEqual(apr_base["balance"] - apr_after["balance"], 4000.0, places=2)

    @unittest.skipUnless(SAMPLE_CSV_PATH.exists(), "Sample CSV not present")
    def test_pizzeria_amalfi_fx_division_yearly_totals_and_three_categories(self) -> None:
        store = LedgerStore()
        csv_text = SAMPLE_CSV_PATH.read_text(encoding="utf-8-sig")
        info = store.import_csv(csv_text, filename=SAMPLE_CSV_PATH.name, opening_balance=2500.0)
        self.assertEqual(info["imported_rows"], 1125)

        dec = store.get_view(selected_month="2025-12", horizon_days=30)
        self.assertEqual(len(dec["history_series"]), 365)
        self.assertEqual(dec["history_series"][0]["date"], "2025-01-01")
        self.assertEqual(dec["history_series"][-1]["date"], "2025-12-31")
        self.assertAlmostEqual(dec["month_summary"]["total_income"], 55204.54, places=2)
        self.assertAlmostEqual(dec["month_summary"]["total_expenses"], 45624.08, places=2)
        self.assertAlmostEqual(dec["month_summary"]["profit"], 9580.46, places=2)
        self.assertFalse(dec["liquidity_agent"]["goes_below_zero"])
        self.assertEqual(dec["liquidity_agent"]["recommended_credit_amount"], 0.0)

        self.assertEqual(len(dec["yearly_summaries"]), 1)
        y2025 = dec["yearly_summaries"][0]
        self.assertEqual(y2025["year"], 2025)
        self.assertAlmostEqual(y2025["total_income"], 544424.27, places=2)
        self.assertAlmostEqual(y2025["total_expenses"], 509391.15, places=2)
        self.assertAlmostEqual(y2025["profit"], 35033.12, places=2)

        exp_cats = {c["category"]: c for c in dec["expense_breakdown"]}
        self.assertTrue(set(exp_cats.keys()).issubset({"Salaries", "Taxes", "Other"}))
        self.assertAlmostEqual(exp_cats["Salaries"]["total"], 8600.0, places=2)
        salary_names = {p["name"]: p["total"] for p in exp_cats["Salaries"]["people"]}
        self.assertEqual(salary_names["Vincenzo Amalfi"], 3200.0)
        self.assertEqual(salary_names["Yusuf Akcit"], 2350.0)
        self.assertEqual(salary_names["Rosa Pavese"], 2100.0)
        self.assertEqual(salary_names["Vanessa Paradi"], 950.0)
        self.assertAlmostEqual(exp_cats["Taxes"]["total"], 2030.0, places=2)

        other_names = {p["name"]: p["total"] for p in exp_cats["Other"]["people"]}
        self.assertAlmostEqual(
            other_names["Techniker Krankenkasse Social Health Fund"], 2650.0, places=2
        )
        self.assertAlmostEqual(
            other_names["Luigi Amalfi (Luigi's Ristorante Zurich)"], 3979.63, places=2
        )

        jun = store.get_view(selected_month="2025-06", horizon_days=35)
        self.assertTrue(jun["liquidity_agent"]["goes_below_zero"])
        self.assertGreater(jun["liquidity_agent"]["recommended_credit_amount"], 20000.0)

        prompt = build_context_system_prompt(selected_month="2025-12", horizon_days=30, store=store)
        self.assertIn("544,424.27", prompt)
        self.assertIn("35,033.12", prompt)
        self.assertIn("2025-06 (June 2025)", prompt)
        self.assertIn("2025-12 (December 2025)", prompt)

    def test_zero_horizon_off_mode_safe_with_negative_balance(self) -> None:
        txs = synthetic_ledger(date(2025, 1, 1), date(2025, 12, 31))
        store = make_store_from_txs(txs, opening_balance=-500000.0)
        view_month = store.get_view(horizon_months=0)
        la_m = view_month["liquidity_agent"]
        self.assertEqual(la_m["horizon_months"], 0)
        self.assertEqual(la_m["forecast_series"], [])
        self.assertEqual(len(la_m["full_forecast_series"]), 12)
        self.assertIn("turned off", la_m["recommendation_summary"].lower())

        short_store = LedgerStore()
        short_store.add_manual_operation("2026-01-10", "expense", 1000.0, "Rent", "Other")
        view_day = short_store.get_view(horizon_days=0)
        la_d = view_day["liquidity_agent"]
        self.assertEqual(la_d["horizon_days"], 0)
        self.assertEqual(la_d["forecast_series"], [])
        self.assertEqual(len(la_d["full_forecast_series"]), 365)
        self.assertIn("turned off", la_d["recommendation_summary"].lower())


if __name__ == "__main__":
    unittest.main()
