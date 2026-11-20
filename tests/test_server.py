import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import greg
from greg import engine


class ChatBackendTest(unittest.TestCase):
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
