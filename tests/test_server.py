import io
import json
import os
import threading
import unittest
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import greg
from greg.ledger import LedgerStore, STORE

SAMPLE_CSV_PATH = (
    Path(__file__).resolve().parent.parent
    / "requirements"
    / "red-hat-challenge-samle data"
    / "pizzeria-amalfi-bank-statement-2025-v6.csv"
)

INDEX_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "greg" / "static" / "index.html"
)


class ChatBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        STORE.reset()

    def test_health_payload_reflects_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_BASE_URL": "http://llama-32-3b-instruct-predictor.my-first-model.svc.cluster.local:8080/v1",
                "LLM_MODEL": "llama-32-3b-instruct",
            },
            clear=False,
        ):
            payload = greg._health_payload()
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["llm_configured"])
            self.assertEqual(payload["llm_model"], "llama-32-3b-instruct")

    def test_call_llm_requires_base_url(self) -> None:
        with patch.dict(os.environ, {"LLM_BASE_URL": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                greg.call_llm([{"role": "user", "content": "Hello"}])

    def test_call_llm_sends_chat_completion_and_returns_reply(self) -> None:
        fake_response = json.dumps(
            {
                "model": "llama-32-3b-instruct",
                "choices": [{"message": {"role": "assistant", "content": "Cash flow looks steady."}}],
            }
        ).encode("utf-8")

        class FakeHTTPResponse(io.BytesIO):
            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, *args: object) -> None:
                self.close()

        captured = {}

        def fake_urlopen(req: object, timeout: int = 0) -> FakeHTTPResponse:
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse(fake_response)

        with patch.dict(
            os.environ,
            {
                "LLM_BASE_URL": "http://vllm.local:8080/v1/",
                "LLM_MODEL": "llama-32-3b-instruct",
            },
            clear=False,
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = greg.call_llm([{"role": "user", "content": "How is cash flow?"}])

        self.assertEqual(captured["url"], "http://vllm.local:8080/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "llama-32-3b-instruct")
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")
        self.assertEqual(captured["body"]["messages"][1]["content"], "How is cash flow?")
        self.assertEqual(result["reply"], "Cash flow looks steady.")

    def test_classify_with_llm_parses_json_and_handles_fences(self) -> None:
        fake_response = json.dumps(
            {
                "model": "llama-32-3b-instruct",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": 'Here is the classification:\n```json\n{"0": "Salaries", "1": "Taxes", "2": "InvalidCat"}\n```',
                        }
                    }
                ],
            }
        ).encode("utf-8")

        class FakeHTTPResponse(io.BytesIO):
            def __enter__(self) -> "FakeHTTPResponse":
                return self

            def __exit__(self, *args: object) -> None:
                self.close()

        sigs = [
            {"key": "k0", "counterparty": "Payroll Co", "raw_type": "TRANSFER", "reference": "Oct", "direction": "expense"},
            {"key": "k1", "counterparty": "Agency Z", "raw_type": "DEBIT", "reference": "Q3", "direction": "expense"},
            {"key": "k2", "counterparty": "Bakery", "raw_type": "CARD", "reference": "Bread", "direction": "expense"},
        ]

        with patch.dict(
            os.environ,
            {"LLM_BASE_URL": "http://vllm.local:8080/v1", "LLM_MODEL": "llama-32-3b-instruct"},
            clear=False,
        ), patch("urllib.request.urlopen", return_value=FakeHTTPResponse(fake_response)):
            mapping = greg._classify_with_llm(sigs)

        self.assertEqual(mapping, {"k0": "Salaries", "k1": "Taxes"})


class LedgerAndLiquidityAgentTest(unittest.TestCase):
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

        # Check January breakdown & 3-category grouping
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

        # Add a manual future expense in March and verify forecast detects shortfall
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

        # Approve simulated credit and verify forecast series includes credit cushion
        store.approve_credit(amount=la["recommended_credit_amount"], month="2026-02", horizon_days=30)
        feb_after = store.get_view(selected_month="2026-02", horizon_days=30)
        for pt in feb_after["liquidity_agent"]["forecast_series"]:
            self.assertAlmostEqual(
                pt["balance_with_credit"] - pt["balance"],
                la["recommended_credit_amount"],
                places=2,
            )

        # Delete manual operation
        self.assertTrue(store.delete_operation(op["id"]))

    def test_hybrid_llm_classifier_and_social_security_guardrail(self) -> None:
        csv_text = (
            "Date,Type,Counterparty,Description,Amount,Currency\n"
            "2026-01-10,TRANSFER,Unknown Payroll Provider,Monthly Transfer,-3000.00,EUR\n"
            "2026-01-15,DIRECT DEBIT,Techniker Krankenkasse Social Health Fund,Health Insurance,-900.00,EUR\n"
            "2026-01-20,TRANSFER,Agency X,Quarterly Assessment,-600.00,EUR\n"
        )

        def fake_llm_classifier(sigs: list[dict[str, str]]) -> dict[str, str]:
            out = {}
            for s in sigs:
                if "Unknown Payroll Provider" in s["counterparty"]:
                    out[s["key"]] = "Salaries"
                elif "Agency X" in s["counterparty"]:
                    out[s["key"]] = "Taxes"
                elif "Krankenkasse" in s["counterparty"]:
                    # Even if LLM mislabels social security as Taxes, guardrail keeps it in Other
                    out[s["key"]] = "Taxes"
            return out

        store = LedgerStore()
        store.import_csv(csv_text, llm_classifier=fake_llm_classifier)
        jan = store.get_view(selected_month="2026-01")
        cat_totals = {c["category"]: c["total"] for c in jan["expense_breakdown"]}
        self.assertEqual(cat_totals["Salaries"], 3000.0)
        self.assertEqual(cat_totals["Taxes"], 600.0)
        self.assertEqual(cat_totals["Other"], 900.0)

    @unittest.skipUnless(SAMPLE_CSV_PATH.exists(), "Sample CSV not present")
    def test_pizzeria_amalfi_fx_division_yearly_totals_and_three_categories(self) -> None:
        store = LedgerStore()
        csv_text = SAMPLE_CSV_PATH.read_text(encoding="utf-8-sig")
        info = store.import_csv(csv_text, filename=SAMPLE_CSV_PATH.name, opening_balance=2500.0)
        self.assertEqual(info["imported_rows"], 1125)

        # Verify December 2025 FX division (-4166.67 CHF / 1.047 = -3979.63 EUR) & exact profit
        dec = store.get_view(selected_month="2025-12", horizon_days=30)
        self.assertEqual(len(dec["history_series"]), 365)
        self.assertEqual(dec["history_series"][0]["date"], "2025-01-01")
        self.assertEqual(dec["history_series"][-1]["date"], "2025-12-31")
        self.assertAlmostEqual(dec["month_summary"]["total_income"], 55204.54, places=2)
        self.assertAlmostEqual(dec["month_summary"]["total_expenses"], 45624.08, places=2)
        self.assertAlmostEqual(dec["month_summary"]["profit"], 9580.46, places=2)
        self.assertFalse(dec["liquidity_agent"]["goes_below_zero"])
        self.assertEqual(dec["liquidity_agent"]["recommended_credit_amount"], 0.0)

        # Verify Full-Year 2025 summary
        self.assertEqual(len(dec["yearly_summaries"]), 1)
        y2025 = dec["yearly_summaries"][0]
        self.assertEqual(y2025["year"], 2025)
        self.assertAlmostEqual(y2025["total_income"], 544424.27, places=2)
        self.assertAlmostEqual(y2025["total_expenses"], 509391.15, places=2)
        self.assertAlmostEqual(y2025["profit"], 35033.12, places=2)

        # Verify strictly 3 categories: Salaries, Taxes, Other
        exp_cats = {c["category"]: c for c in dec["expense_breakdown"]}
        self.assertTrue(set(exp_cats.keys()).issubset({"Salaries", "Taxes", "Other"}))

        self.assertAlmostEqual(exp_cats["Salaries"]["total"], 8600.0, places=2)
        salary_names = {p["name"]: p["total"] for p in exp_cats["Salaries"]["people"]}
        self.assertEqual(salary_names["Vincenzo Amalfi"], 3200.0)
        self.assertEqual(salary_names["Yusuf Akcit"], 2350.0)
        self.assertEqual(salary_names["Rosa Pavese"], 2100.0)
        self.assertEqual(salary_names["Vanessa Paradi"], 950.0)

        # Taxes in Dec 2025 = 880 (Wage Tax) + 1150 (Q4 Income Tax prepayment) = 2030.0
        self.assertAlmostEqual(exp_cats["Taxes"]["total"], 2030.0, places=2)

        # Social security fund (Techniker Krankenkasse 2650.0) is in Other
        other_names = {p["name"]: p["total"] for p in exp_cats["Other"]["people"]}
        self.assertAlmostEqual(other_names["Techniker Krankenkasse Social Health Fund"], 2650.0, places=2)
        self.assertAlmostEqual(other_names["Luigi Amalfi (Luigi's Ristorante Zurich)"], 3979.63, places=2)

        # Verify June 2025 deficit and credit recommendation
        jun = store.get_view(selected_month="2025-06", horizon_days=35)
        self.assertTrue(jun["liquidity_agent"]["goes_below_zero"])
        self.assertGreater(jun["liquidity_agent"]["recommended_credit_amount"], 20000.0)

        # Verify LLM context prompt includes full-year 2025 totals and all 12 months
        STORE.transactions = store.transactions
        STORE.opening_balance = store.opening_balance
        STORE.filename = store.filename
        STORE.account_currency = store.account_currency
        prompt = greg._build_context_system_prompt(selected_month="2025-12", horizon_days=30)
        self.assertIn("544,424.27", prompt)
        self.assertIn("35,033.12", prompt)
        self.assertIn("2025-06 (June 2025)", prompt)
        self.assertIn("2025-12 (December 2025)", prompt)


class FrontendCopyTest(unittest.TestCase):
    def test_frontend_labels_cleaned_up(self) -> None:
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "Plain-language monthly income &amp; expense breakdown and cash forecast",
            "(; or , separated)",
            "Group / Category",
            "Choose any month to inspect who paid you and who you paid",
            "Money In",
            "Money Out",
            "Left Over This Month",
            "— Details",
            "Manually Add an Income or Expense",
            "Grouped by category",
            "Liquidity Agent — Future Cash Forecast",
            "1. Import",
            "2. Manually",
            "3. Monthly",
            "4. Liquidity",
            "5. Ask Greg",
            "Month End (",
            "Past balance",
            'id="interval-select"',
            'id="horizon-select"',
            'id="chart-scroll"',
            'id="btn-zoom-in"',
            'id="btn-zoom-out"',
            'id="btn-zoom-reset"',
            ">View By<",
            ">Forecast Ahead<",
        ):
            self.assertNotIn(forbidden, html, msg=f"Unexpected forbidden UI text: {forbidden}")

        for required in (
            "Import Bank Statement (CSV)",
            "Add Manually",
            "Monthly Overview",
            ">Income<",
            ">Expenses<",
            ">Profit<",
            ">Forecast<",
            '<option value="Salaries">Salaries</option>',
            '<option value="Taxes">Taxes</option>',
            '<option value="Other">Other</option>',
            'id="chart-tooltip"',
            'id="chart-crosshair"',
            'id="chart-hover-dot"',
            'id="chart-plot-area"',
            'id="history-pill-group"',
            'id="forecast-pill-group"',
            'data-hist-days="1">1D</button>',
            'data-hist-days="7">7D</button>',
            'data-hist-days="30">1M</button>',
            'data-hist-days="90">3M</button>',
            'data-hist-days="180">6M</button>',
            'data-hist-days="365">1Y</button>',
            'data-fore-days="0">Off</button>',
            'data-fore-days="1">+1D</button>',
            'data-fore-days="7">+7D</button>',
            'data-fore-days="30">+1M</button>',
            'data-fore-days="90">+3M</button>',
            'data-fore-days="180">+6M</button>',
            'data-fore-days="365">+1Y</button>',
            'id="minimap-track"',
            'id="minimap-svg"',
            'id="minimap-window"',
            'id="minimap-handle-left"',
            'id="minimap-handle-right"',
            'id="chart-range-label"',
            'class="chart-legend hidden"',
            ">Today<",
        ):
            self.assertIn(required, html, msg=f"Missing required UI text: {required}")


class HTTPServerIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        STORE.reset()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), greg.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        STORE.reset()

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_end_to_end_http_workflow(self) -> None:
        # 1. Initial view is empty
        status, view = self._request("GET", "/api/view")
        self.assertEqual(status, 200)
        self.assertFalse(view["has_data"])

        # 2. Import CSV
        csv_text = (
            "Booking Date;Transaction Type;Beneficiary/Payer;Payment Reference;Amount;Currency\n"
            "01.01.25;RENT PAYMENT;Office Landlord;Jan Rent;-1500,00;EUR\n"
            "15.01.25;CARD SETTLEMENT;Customers;Jan Sales;1200,00;EUR\n"
            "01.02.25;RENT PAYMENT;Office Landlord;Feb Rent;-1500,00;EUR\n"
            "15.02.25;CARD SETTLEMENT;Customers;Feb Sales;1100,00;EUR\n"
        )
        status, imported = self._request(
            "POST",
            "/api/import",
            {"csv_text": csv_text, "filename": "test.csv", "opening_balance": 500.0},
        )
        self.assertEqual(status, 200)
        self.assertTrue(imported["has_data"])
        self.assertTrue(imported["liquidity_agent"]["goes_below_zero"])

        # 3. Add manual operation
        status, after_op = self._request(
            "POST",
            "/api/operations",
            {
                "date": "2025-02-20",
                "kind": "income",
                "amount": 300.0,
                "counterparty": "Bonus Customer",
                "category": "Other",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(after_op["month_summary"]["total_income"], 1400.0)

        # 4. Approve simulated credit
        status, after_credit = self._request(
            "POST",
            "/api/approve-credit",
            {"amount": 2000.0, "selected_month": "2025-02", "horizon_days": 30},
        )
        self.assertEqual(status, 200)
        self.assertEqual(after_credit["approved_credit"]["amount"], 2000.0)

        # 5. Reset back to empty
        status, after_reset = self._request("POST", "/api/reset", {})
        self.assertEqual(status, 200)
        self.assertFalse(after_reset["has_data"])

    def test_serves_inter_font_asset(self) -> None:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/assets/InterVariable.woff2", timeout=5
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "font/woff2")
            data = resp.read()
            self.assertGreater(len(data), 1000)

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
            self.assertIn("Import Bank Statement (CSV)", html)
            self.assertIn("InterVariable.woff2", html)


if __name__ == "__main__":
    unittest.main()
