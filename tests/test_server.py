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
            'id="btn-zoom-in"',
            'id="btn-zoom-out"',
            'id="btn-zoom-reset"',
            'id="interval-select"',
            'id="chart-scroll"',
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


class AutonomousCfoEngineTest(unittest.TestCase):
    def test_pizzeria_amalfi_csv_parsing_and_exact_closing_balance(self) -> None:
        csv_text = engine.load_pizzeria_csv_text()
        parsed = engine.parse_bank_csv(csv_text, opening_balance=Decimal("2500.00"))
        self.assertEqual(parsed["row_count"], 1125)
        self.assertEqual(parsed["unique_count"], 1125)
        self.assertEqual(parsed["closing_balance"], 33266.35)
        self.assertEqual(parsed["min_daily_date"], "2025-07-03")
        self.assertEqual(parsed["min_daily_balance"], -22097.60)
        self.assertEqual(len(parsed["fx_transactions"]), 12)
        self.assertTrue(parsed["is_pizzeria_dataset"])
        self.assertGreaterEqual(parsed["hygiene"]["score"], 95.0)

    def test_pizzeria_amalfi_13w_forecast_and_actions(self) -> None:
        bundle = engine.build_scenario_bundle("pizzeria_amalfi")
        self.assertEqual(bundle["kpis"]["current_balance_eur"], 33266.35)
        self.assertEqual(bundle["kpis"]["projected_min_13w_eur"], -8420.0)
        self.assertEqual(bundle["kpis"]["projected_min_13w_week"], 7)
        self.assertGreaterEqual(bundle["kpis"]["stabilized_min_13w_eur"], 12000.0)
        self.assertEqual(len(bundle["actions"]), 5)
        self.assertNotIn("KronoStahl", json.dumps(bundle))

    def test_custom_csv_upload_forecast_and_kpi_consistency(self) -> None:
        # 1. Re-uploading the Pizzeria Amalfi CSV reproduces the exact 13-week projection
        pizzeria_csv = engine.load_pizzeria_csv_text()
        pa_upload = engine.build_scenario_bundle(
            "custom_csv",
            custom_csv_text=pizzeria_csv,
            custom_filename="pizzeria-amalfi-bank-statement-2025-v6.csv",
            opening_balance=2500.0,
        )
        self.assertEqual(pa_upload["kpis"]["current_balance_eur"], 33266.35)
        self.assertEqual(pa_upload["kpis"]["projected_min_13w_eur"], -8420.0)
        self.assertEqual(pa_upload["kpis"]["projected_min_13w_week"], 7)

        # 2. Uploading a custom multi-week CSV with net deficit produces matching graph & KPI low
        custom_csv = (
            '"Account Number";"Booking Date";"Transaction Type";"Payment Reference";"Beneficiary/Payer";"Amount";"Currency"\n'
            '"DE11000011112222333344";"01.10.25";"CARD SETTLEMENT";"Week 1 Sales";"Customers";"2000,00";"EUR"\n'
            '"DE11000011112222333344";"03.10.25";"RENT PAYMENT";"October Rent";"Landlord";"-3500,00";"EUR"\n'
            '"DE11000011112222333344";"10.10.25";"CARD SETTLEMENT";"Week 2 Sales";"Customers";"1800,00";"EUR"\n'
            '"DE11000011112222333344";"14.10.25";"SUPPLIER PAYMENT";"Ingredients";"Supplier A";"-2200,00";"EUR"\n'
        )
        custom_bundle = engine.build_scenario_bundle(
            "custom_csv",
            custom_csv_text=custom_csv,
            custom_filename="my_shop.csv",
            opening_balance=5000.0,
        )
        series = custom_bundle["simulation"]["series"]
        min_pt = min(series, key=lambda p: p["baseline"])
        self.assertEqual(custom_bundle["kpis"]["current_balance_eur"], 3100.0)
        self.assertEqual(custom_bundle["kpis"]["projected_min_13w_eur"], min_pt["baseline"])
        self.assertEqual(custom_bundle["kpis"]["projected_min_13w_week"], min_pt["week"])
        self.assertLess(custom_bundle["kpis"]["projected_min_13w_eur"], custom_bundle["kpis"]["current_balance_eur"])

    def test_what_if_simulation_and_commerzbank_stabilization(self) -> None:
        bundle = engine.build_scenario_bundle("pizzeria_amalfi")
        nl = engine.parse_what_if_query("What if revenue drops 20% and CHF rises 8% with Commerzbank hedge?")
        self.assertEqual(nl["revenue_change_pct"], -20.0)
        self.assertEqual(nl["fx_shock_pct"], 8.0)
        self.assertTrue(nl["include_commerzbank_actions"])

        stressed = engine.simulate_what_if(
            bundle["simulation"]["series"],
            nl,
            bundle["actions"],
            safety_buffer_eur=12000.0,
        )
        self.assertLess(
            stressed["summary"]["min_simulated_eur"],
            stressed["summary"]["min_baseline_eur"],
        )
        self.assertGreater(
            stressed["summary"]["min_stabilized_eur"],
            stressed["summary"]["min_simulated_eur"],
        )

    def test_pii_and_iban_anonymization(self) -> None:
        raw = "Check DE89370400440532013000 and CH9300700110001234567 for Vincenzo Amalfi at Pizzeria Amalfi."
        masked, tok_map = engine.anonymize_text(raw)
        self.assertNotIn("DE89370400440532013000", masked)
        self.assertNotIn("CH9300700110001234567", masked)
        self.assertNotIn("Vincenzo Amalfi", masked)
        self.assertNotIn("Pizzeria Amalfi", masked)
        self.assertIn("IBAN_TOK_3000", masked)
        self.assertIn("IBAN_TOK_4567", masked)
        self.assertIn("OWNER_REF_001", masked)
        self.assertIn("SME_ENTITY_001", masked)
        self.assertEqual(engine.deanonymize_text(masked, tok_map), raw)

    def test_action_approval_generates_receipt_and_audit_ledger_hash(self) -> None:
        bundle = engine.build_scenario_bundle("pizzeria_amalfi")
        ledger: list[dict] = []
        entry1 = engine.create_audit_entry(ledger, "HUMAN_OWNER", "ACTION_APPROVED", "act-pa-liquidity", "Approved credit")
        entry2 = engine.create_audit_entry(ledger, "HUMAN_OWNER", "ACTION_APPROVED", "act-pa-fx", "Approved FX")
        self.assertEqual(entry2["prev_hash"], entry1["sha256"])
        receipt = engine.create_execution_receipt(bundle["actions"][0], entry1["sha256"])
        self.assertTrue(receipt["receipt_id"].startswith("CBK-EXEC-"))
        self.assertEqual(receipt["mode"], "SIMULATED_COMMERZBANK_API")


class HttpApiIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(
            os.environ,
            {
                "GREG_STATE_FILE": str(Path(self.tmpdir.name) / "state.json"),
                "LLM_BASE_URL": "",
                "LLM_MODEL": "llama-32-3b-instruct",
            },
            clear=False,
        )
        self.env_patch.start()
        greg.reset_state("pizzeria_amalfi")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), greg.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.env_patch.stop()
        self.tmpdir.cleanup()

    def _req(self, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_http_api_endpoints_end_to_end(self) -> None:
        # 1. Health & Simplified Index HTML checks
        st, health = self._req("/health")
        self.assertEqual(st, 200)
        self.assertEqual(health["status"], "ok")

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as resp:
            html = resp.read().decode("utf-8")
            self.assertIn("Greg — Your Autonomous Financial Assistant", html)
            self.assertIn("formatMarkdown", html)
            self.assertNotIn("KronoStahl", html)
            self.assertNotIn("Calm Mode", html)
            self.assertNotIn("Analyst Mode", html)
            self.assertNotIn("chat-chips", html)
            self.assertNotIn("panel-privacy", html)

        # 2. Default state loads Pizzeria Amalfi
        st, state = self._req("/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(state["scenario_id"], "pizzeria_amalfi")
        self.assertEqual(state["kpis"]["current_balance_eur"], 33266.35)
        self.assertEqual(state["kpis"]["projected_min_13w_eur"], -8420.0)

        # 3. Removed scenario is rejected cleanly
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._req("/api/scenario", "POST", {"scenario_id": "mittelstand_b2b"})
        self.assertEqual(ctx.exception.code, 400)

        # 4. Run What-If Simulation & confirm KPI aligns with lowest chart point
        st, sim_out = self._req("/api/simulate", "POST", {"query": "What if sales drop 20% and CHF rises 8%?"})
        self.assertEqual(st, 200)
        self.assertEqual(sim_out["simulation"]["params"]["revenue_change_pct"], -20.0)
        self.assertEqual(sim_out["simulation"]["params"]["fx_shock_pct"], 8.0)
        self.assertEqual(sim_out["kpis"]["projected_min_13w_eur"], sim_out["simulation"]["summary"]["min_simulated_eur"])

        # 5. Approve Action Card & verify Receipt + Audit Ledger
        st, dec = self._req(
            "/api/actions/decide",
            "POST",
            {"action_id": "act-pa-liquidity", "decision": "approved", "amount_eur": 30000},
        )
        self.assertEqual(st, 200)
        self.assertIsNotNone(dec["latest_receipt"])
        self.assertTrue(dec["latest_receipt"]["receipt_id"].startswith("CBK-EXEC-"))
        self.assertEqual(dec["receipts"][0]["amount_eur"], 30000.0)

        # 6. Update Profile & Preferences
        st, prof = self._req("/api/profile", "POST", {"safety_buffer_eur": 15000})
        self.assertEqual(st, 200)
        self.assertEqual(prof["kpis"]["safety_buffer_eur"], 15000.0)

        st, pref = self._req("/api/preferences", "POST", {"min_severity": "high"})
        self.assertEqual(st, 200)
        self.assertTrue(all(a["severity"] in ("high", "critical") or a["status"] != "pending" for a in pref["actions"]))

        # 7. AI Analysis & Context Chat fallback + PII masking trace
        st, ai_state = self._req("/api/ai/analyze", "POST", {})
        self.assertEqual(st, 200)
        self.assertEqual(ai_state["ai_briefing"]["mode"], "deterministic_fallback")
        self.assertNotIn("DE89370400440532013000", ai_state["last_ai_trace"]["anonymized_system_prompt"])

        # 8. Live OpenShift AI LLM mode via mocked vLLM response
        fake_llm_reply = json.dumps(
            {
                "model": "llama-32-3b-instruct",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "**SME_ENTITY_001** on IBAN_TOK_3000 should approve the €25,000 credit line.",
                        }
                    }
                ],
            }
        ).encode("utf-8")

        class FakeResp(io.BytesIO):
            def __enter__(self) -> "FakeResp":
                return self

            def __exit__(self, *args: object) -> None:
                self.close()

        captured_payload = {}
        orig_urlopen = urllib.request.urlopen

        def fake_vllm_urlopen(req: object, timeout: int = 0) -> FakeResp:
            if getattr(req, "full_url", "").endswith("/chat/completions"):
                captured_payload["body"] = json.loads(req.data.decode("utf-8"))
                return FakeResp(fake_llm_reply)
            return orig_urlopen(req, timeout=timeout)

        with patch.dict(
            os.environ,
            {"LLM_BASE_URL": "http://llama-32-3b-instruct-predictor.my-first-model.svc.cluster.local:8080/v1"},
            clear=False,
        ), patch("urllib.request.urlopen", side_effect=fake_vllm_urlopen):
            res = greg.invoke_cfo_llm([{"role": "user", "content": "Advise Pizzeria Amalfi on DE89370400440532013000"}])

        self.assertEqual(res["mode"], "openshift_ai_live")
        self.assertIn("Pizzeria Amalfi", res["reply"])
        self.assertIn("DE89370400440532013000", res["reply"])
        sent_messages_text = json.dumps(captured_payload["body"]["messages"])
        self.assertNotIn("DE89370400440532013000", sent_messages_text)
        self.assertNotIn("Pizzeria Amalfi", sent_messages_text)

        # 9. Custom CSV Upload resets stale simulation params and matches 13-week low
        sample_csv = (
            '"Account Number";"Booking Date";"Transaction Type";"Payment Reference";"Beneficiary/Payer";"Amount";"Currency"\n'
            '"DE89000011112222333344";"01.11.25";"CARD SETTLEMENT";"Daily Sales";"Customers";"1500,00";"EUR"\n'
            '"DE89000011112222333344";"02.11.25";"SUPPLIER PAYMENT";"Invoice 123";"Supplier A";"-2500,00";"EUR"\n'
        )
        st, up = self._req("/api/upload-csv", "POST", {"filename": "test.csv", "csv_text": sample_csv, "opening_balance": 3000.0})
        self.assertEqual(st, 200)
        self.assertEqual(up["scenario_id"], "custom_csv")
        self.assertFalse(up["simulation"]["is_stressed"])
        self.assertEqual(up["kpis"]["current_balance_eur"], 2000.0)
        min_pt = min(up["simulation"]["series"], key=lambda p: p["baseline"])
        self.assertEqual(up["kpis"]["projected_min_13w_eur"], min_pt["baseline"])
        self.assertEqual(up["kpis"]["projected_min_13w_week"], min_pt["week"])

        # 10. Reset back to Pizzeria Amalfi
        st, reset_st = self._req("/api/scenario", "POST", {"scenario_id": "pizzeria_amalfi"})
        self.assertEqual(st, 200)
        self.assertEqual(reset_st["scenario_id"], "pizzeria_amalfi")
        self.assertEqual(reset_st["kpis"]["projected_min_13w_eur"], -8420.0)


if __name__ == "__main__":
    unittest.main()
