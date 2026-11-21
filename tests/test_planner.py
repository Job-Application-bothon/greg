import io
import math
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path

import greg.planner as gp
from greg.ledger import LedgerStore

MODEL_PATH = Path(__file__).resolve().parent.parent / "src" / "greg" / "planner_model" / "planner.keras"


def _synthetic_ledger(start: date, end: date, *, growth: float = 1.05, scale: float = 1.0) -> list[dict]:
    """Generate a purely synthetic SME bank ledger between `start` and `end`."""
    txs: list[dict] = []
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


def _make_store_from_txs(txs: list[dict], opening_balance: float = 10000.0) -> LedgerStore:
    store = LedgerStore()
    store.transactions = list(txs)
    store.opening_balance = opening_balance
    store.filename = "synthetic.csv"
    store.account_currency = "EUR"
    return store


class PlannerNumpyLSTMTest(unittest.TestCase):
    def test_numpy_lstm_loads_and_matches_reference_step_math(self) -> None:
        import h5py
        import numpy as np

        self.assertTrue(MODEL_PATH.exists(), f"Missing model file at {MODEL_PATH}")
        runner = gp._NumpySeq2SeqLSTM.from_keras_archive(MODEL_PATH)

        # Verify expected shapes extracted from planner.keras
        self.assertEqual(runner.enc_weights[0].shape, (1, 256))
        self.assertEqual(runner.enc_weights[1].shape, (64, 256))
        self.assertEqual(runner.enc_weights[2].shape, (256,))
        self.assertEqual(runner.repeat_n, 12)
        self.assertEqual(runner.dec_fwd_weights[0].shape, (66, 128))
        self.assertEqual(runner.dec_bwd_weights[0].shape, (66, 128))
        self.assertEqual(len(runner.dense_layers), 2)
        self.assertEqual(runner.dense_layers[0][0].shape, (64, 32))
        self.assertEqual(runner.dense_layers[1][0].shape, (32, 1))

        rng = np.random.default_rng(42)
        past = rng.normal(1.0, 0.2, size=(2, 24, 1)).astype(np.float32)
        fut = rng.normal(1.0, 0.15, size=(2, 12, 2)).astype(np.float32)
        pred = runner.predict([past, fut], verbose=0)

        self.assertEqual(pred.shape, (2, 12))
        self.assertTrue(np.isfinite(pred).all())
        self.assertGreater(float(np.abs(pred).max()), 1e-4)

        # Independent naive float32 scalar reference computation directly from raw HDF5 weights
        with zipfile.ZipFile(MODEL_PATH, "r") as zf:
            hf_bytes = zf.read("model.weights.h5")

        def sig(x: float) -> float:
            return float(1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, float(x))))))

        def ref_lstm_step_seq(seq: np.ndarray, grp: h5py.Group, go_backwards: bool) -> np.ndarray:
            w = np.asarray(grp["cell/vars/0"], dtype=np.float32)
            u = np.asarray(grp["cell/vars/1"], dtype=np.float32)
            b = np.asarray(grp["cell/vars/2"], dtype=np.float32)
            units = u.shape[0]
            bsz, steps, _ = seq.shape
            outs = np.zeros((bsz, steps, units), dtype=np.float32)
            for bi in range(bsz):
                h = np.zeros(units, dtype=np.float32)
                c = np.zeros(units, dtype=np.float32)
                order = list(range(steps - 1, -1, -1)) if go_backwards else list(range(steps))
                for out_t, t in enumerate(order):
                    z = seq[bi, t] @ w + h @ u + b
                    i = np.array([sig(v) for v in z[:units]], dtype=np.float32)
                    f = np.array([sig(v) for v in z[units : 2 * units]], dtype=np.float32)
                    g = np.tanh(z[2 * units : 3 * units]).astype(np.float32)
                    o = np.array([sig(v) for v in z[3 * units :]], dtype=np.float32)
                    c = f * c + i * g
                    h = o * np.tanh(c)
                    outs[bi, out_t] = h
            return outs

        with h5py.File(io.BytesIO(hf_bytes), "r") as hf:
            enc_last = ref_lstm_step_seq(past, hf["layers/lstm"], go_backwards=False)[:, -1, :]
            dec_in = np.concatenate([np.repeat(enc_last[:, None, :], 12, axis=1), fut], axis=-1)
            fwd = ref_lstm_step_seq(dec_in, hf["layers/bidirectional/forward_layer"], go_backwards=False)
            bwd_raw = ref_lstm_step_seq(dec_in, hf["layers/bidirectional/backward_layer"], go_backwards=True)
            merged = np.concatenate([fwd, bwd_raw[:, ::-1, :]], axis=-1)
            d1_w = np.asarray(hf["layers/dense/vars/0"], dtype=np.float32)
            d1_b = np.asarray(hf["layers/dense/vars/1"], dtype=np.float32)
            d2_w = np.asarray(hf["layers/dense_1/vars/0"], dtype=np.float32)
            d2_b = np.asarray(hf["layers/dense_1/vars/1"], dtype=np.float32)
            h1 = np.maximum(merged @ d1_w + d1_b, 0.0)
            ref_out = (h1 @ d2_w + d2_b).reshape(2, 12).astype(np.float32)

        max_diff = float(np.max(np.abs(pred - ref_out)))
        self.assertLess(max_diff, 1e-5)


class PlannerRoutingAndAlgorithmsTest(unittest.TestCase):
    def setUp(self) -> None:
        gp._PLANNER = None

    def test_twelve_months_with_holiday_boundaries_activates_planner(self) -> None:
        # Jan 2 -> Dec 30 has 12 calendar months with realistic bank-holiday endpoints
        txs = _synthetic_ledger(date(2025, 1, 2), date(2025, 12, 30), growth=1.0)
        r = gp.plan_from_transactions(txs, method="auto", start_balance=12000.0)
        self.assertTrue(r["available"], msg=r.get("reason"))
        self.assertEqual(r["last_history_month"], "2025-12")
        self.assertEqual(r["months"], [f"2026-{m:02d}" for m in range(1, 13)])
        inflow = r["series"]["inflow"]
        self.assertEqual(inflow["method"], "classic")
        # Summer peak preserved (July > March)
        self.assertGreater(inflow["plan"][6], inflow["plan"][2])

    def test_two_and_a_half_years_uses_neural_network_via_numpy(self) -> None:
        txs = _synthetic_ledger(date(2023, 7, 1), date(2025, 12, 31), growth=1.06)
        r = gp.plan_from_transactions(txs, method="auto", start_balance=15000.0)
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
        txs = _synthetic_ledger(date(2022, 1, 1), date(2025, 12, 31), growth=1.05)
        r = gp.plan_from_transactions(txs, method="auto", start_balance=20000.0)
        self.assertTrue(r["available"], msg=r.get("reason"))
        notes_text = "\n".join(r["notes"])
        self.assertIn("chosen automatically", notes_text)
        for model_label in ("ETS", "ARIMA", "Theta", "neural network", "classic seller method"):
            self.assertIn(model_label, notes_text)
        self.assertNotIn("StatsForecast not installed", notes_text)
        self.assertNotIn("StatsForecast is not installed", notes_text)
        self.assertNotIn("failed:", notes_text)

    def test_short_history_falls_back_to_daily_forecaster_in_ledger_store(self) -> None:
        txs = _synthetic_ledger(date(2025, 7, 1), date(2025, 12, 31))
        plan = gp.plan_from_transactions(txs)
        self.assertFalse(plan["available"])
        self.assertIn("12 full months", plan["reason"])

        store = _make_store_from_txs(txs, opening_balance=8000.0)
        view = store.get_view(horizon_days=30)
        la = view["liquidity_agent"]
        self.assertEqual(la["engine"], "daily_fallback")
        self.assertEqual(la["granularity"], "day")
        self.assertIsNone(la["horizon_months"])
        self.assertEqual(len(la["forecast_series"]), 30)
        self.assertEqual(len(la["forecast_series"][0]["date"]), 10)  # YYYY-MM-DD
        self.assertTrue(view["monthly_history_series"])

    def test_partial_trailing_month_alignment_and_manual_future_overlay(self) -> None:
        # 13 full months (2025-01..2026-01) + partial 2026-02 (through Feb 10)
        txs = _synthetic_ledger(date(2025, 1, 1), date(2026, 2, 10))
        store = _make_store_from_txs(txs, opening_balance=10000.0)

        base_view = store.get_view(selected_month="2026-02", horizon_months=6)
        la = base_view["liquidity_agent"]
        self.assertEqual(la["engine"], "greg_planner")
        self.assertEqual(la["granularity"], "month")
        self.assertEqual(la["horizon_months"], 6)
        self.assertEqual(la["last_history_month"], "2026-01")
        self.assertEqual(len(la["forecast_series"]), 6)
        self.assertEqual(la["forecast_series"][0]["date"], "2026-02")

        # Verify chart monthly history ends cleanly at last_history_month with exact aligned balance
        self.assertEqual(base_view["monthly_history_series"][-1]["date"], "2026-01")
        self.assertEqual(base_view["monthly_history_series"][-1]["balance"], la["current_balance"])

        # Add a future manual expense in April 2026 and check selected_month stayed anchored to 2026-02 view
        store.add_manual_operation("2026-04-15", "expense", 4000.0, "One-off Equipment", "Other")
        after_view = store.get_view(selected_month="2026-02", horizon_months=6)
        apr_base = [pt for pt in base_view["liquidity_agent"]["forecast_series"] if pt["date"] == "2026-04"][0]
        apr_after = [pt for pt in after_view["liquidity_agent"]["forecast_series"] if pt["date"] == "2026-04"][0]
        self.assertAlmostEqual(apr_after["expected_out"] - apr_base["expected_out"], 4000.0, places=2)
        self.assertAlmostEqual(apr_base["balance"] - apr_after["balance"], 4000.0, places=2)

    def test_zero_flow_edge_case_ledgers_produce_finite_results(self) -> None:
        base_txs = _synthetic_ledger(date(2024, 1, 1), date(2025, 12, 31))
        expense_only = [t for t in base_txs if t["amount"] < 0]
        income_only = [t for t in base_txs if t["amount"] > 0]
        three_yr = _synthetic_ledger(date(2023, 1, 1), date(2025, 12, 31))
        zero_recent_inflow = [t for t in three_yr if not (t["amount"] > 0 and t["date_obj"].year >= 2024)]

        for label, subset in (
            ("expense_only", expense_only),
            ("income_only", income_only),
            ("zero_recent_inflow", zero_recent_inflow),
        ):
            r = gp.plan_from_transactions(subset, method="auto", start_balance=5000.0)
            self.assertTrue(r["available"], msg=f"{label}: {r}")
            for flow_key in ("inflow", "outflow"):
                for val in r["series"][flow_key]["plan"] + r["series"][flow_key]["low"] + r["series"][flow_key]["high"]:
                    self.assertTrue(math.isfinite(val), msg=f"Non-finite in {label}/{flow_key}: {val}")
            for val in r["cash"]["balance"] + r["cash"]["balance_pessimistic"]:
                self.assertTrue(math.isfinite(val), msg=f"Non-finite balance in {label}: {val}")


if __name__ == "__main__":
    unittest.main()
