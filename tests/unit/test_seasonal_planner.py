"""Synthetic unit tests for `SeasonalPlanner` routing, backtesting, and edge guards."""
from __future__ import annotations

import math
import unittest
from datetime import date

from greg.forecasting.seasonal import plan_from_transactions, reset_planner_cache
from tests.helpers import synthetic_ledger


class SeasonalPlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_planner_cache()

    def test_twelve_months_with_holiday_boundaries_activates_planner(self) -> None:
        # Jan 2 -> Dec 30 has 12 calendar months with realistic bank-holiday endpoints.
        txs = synthetic_ledger(date(2025, 1, 2), date(2025, 12, 30), growth=1.0)
        r = plan_from_transactions(txs, method="auto", start_balance=12000.0)
        self.assertTrue(r["available"], msg=r.get("reason"))
        self.assertEqual(r["last_history_month"], "2025-12")
        self.assertEqual(r["months"], [f"2026-{m:02d}" for m in range(1, 13)])
        inflow = r["series"]["inflow"]
        self.assertEqual(inflow["method"], "classic")
        # Summer peak preserved (July > March).
        self.assertGreater(inflow["plan"][6], inflow["plan"][2])

    def test_two_and_a_half_years_uses_neural_network_via_numpy(self) -> None:
        txs = synthetic_ledger(date(2023, 7, 1), date(2025, 12, 31), growth=1.06)
        r = plan_from_transactions(txs, method="auto", start_balance=15000.0)
        self.assertTrue(r["available"], msg=r.get("reason"))
        self.assertTrue(r["network_loaded"])
        self.assertEqual(r["series"]["inflow"]["method"], "network")
        self.assertEqual(r["series"]["outflow"]["method"], "network")
        for flow_key in ("inflow", "outflow"):
            s = r["series"][flow_key]
            for i in range(12):
                self.assertLessEqual(s["low"][i], s["plan"][i])
                self.assertLessEqual(s["plan"][i], s["high"][i])
        for i in range(12):
            self.assertLessEqual(r["cash"]["balance_pessimistic"][i], r["cash"]["balance"][i])

    def test_four_years_auto_compares_classic_network_and_statsforecast(self) -> None:
        txs = synthetic_ledger(date(2022, 1, 1), date(2025, 12, 31), growth=1.05)
        r = plan_from_transactions(txs, method="auto", start_balance=20000.0)
        self.assertTrue(r["available"], msg=r.get("reason"))
        notes_text = "\n".join(r["notes"])
        self.assertIn("chosen automatically", notes_text)
        for model_label in ("ETS", "ARIMA", "Theta", "neural network", "classic seller method"):
            self.assertIn(model_label, notes_text)
        self.assertNotIn("StatsForecast not installed", notes_text)
        self.assertNotIn("StatsForecast is not installed", notes_text)
        self.assertNotIn("failed:", notes_text)

    def test_short_history_rejects_seasonal_planner(self) -> None:
        txs = synthetic_ledger(date(2025, 7, 1), date(2025, 12, 31))
        plan = plan_from_transactions(txs)
        self.assertFalse(plan["available"])
        self.assertIn("12 full months", plan["reason"])

    def test_zero_flow_edge_case_ledgers_produce_finite_results(self) -> None:
        base_txs = synthetic_ledger(date(2024, 1, 1), date(2025, 12, 31))
        expense_only = [t for t in base_txs if t["amount"] < 0]
        income_only = [t for t in base_txs if t["amount"] > 0]
        three_yr = synthetic_ledger(date(2023, 1, 1), date(2025, 12, 31))
        zero_recent_inflow = [
            t for t in three_yr if not (t["amount"] > 0 and t["date_obj"].year >= 2024)
        ]

        for label, subset in (
            ("expense_only", expense_only),
            ("income_only", income_only),
            ("zero_recent_inflow", zero_recent_inflow),
        ):
            r = plan_from_transactions(subset, method="auto", start_balance=5000.0)
            self.assertTrue(r["available"], msg=f"{label}: {r}")
            for flow_key in ("inflow", "outflow"):
                vals = (
                    r["series"][flow_key]["plan"]
                    + r["series"][flow_key]["low"]
                    + r["series"][flow_key]["high"]
                )
                for val in vals:
                    self.assertTrue(math.isfinite(val), msg=f"Non-finite in {label}/{flow_key}: {val}")
            for val in r["cash"]["balance"] + r["cash"]["balance_pessimistic"]:
                self.assertTrue(math.isfinite(val), msg=f"Non-finite balance in {label}: {val}")


if __name__ == "__main__":
    unittest.main()
