"""Unit tests for LLM HTTP client, signature classifier, prompts, and health status."""
from __future__ import annotations

import json
import os
import unittest
from datetime import date
from unittest.mock import patch

from greg.api.server import health_payload
from greg.domain.sessions import STORE
from greg.llm.classifier import classify_signatures_with_llm
from greg.llm.client import call_llm
from greg.llm.prompts import build_context_system_prompt, build_explain_prompt
from tests.helpers import FakeHTTPResponse, make_store_from_txs, synthetic_ledger


class LLMUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        STORE.reset()

    def test_health_payload_reflects_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_BASE_URL": "http://llama-32-3b-instruct-predictor.svc.cluster.local:8080/v1",
                "LLM_MODEL": "llama-32-3b-instruct",
            },
            clear=False,
        ):
            payload = health_payload()
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["llm_configured"])
            self.assertEqual(payload["llm_model"], "llama-32-3b-instruct")

    def test_call_llm_requires_base_url(self) -> None:
        with patch.dict(os.environ, {"LLM_BASE_URL": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                call_llm([{"role": "user", "content": "Hello"}])

    def test_call_llm_sends_chat_completion_and_returns_reply(self) -> None:
        fake_response = json.dumps(
            {
                "model": "llama-32-3b-instruct",
                "choices": [{"message": {"role": "assistant", "content": "Cash flow looks steady."}}],
            }
        ).encode("utf-8")

        captured: dict = {}

        def fake_urlopen(req: object, timeout: int = 0) -> FakeHTTPResponse:
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse(fake_response)

        with patch.dict(
            os.environ,
            {"LLM_BASE_URL": "http://vllm.local:8080/v1/", "LLM_MODEL": "llama-32-3b-instruct"},
            clear=False,
        ), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = call_llm([{"role": "user", "content": "How is cash flow?"}])

        self.assertEqual(captured["url"], "http://vllm.local:8080/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "llama-32-3b-instruct")
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")
        self.assertEqual(captured["body"]["messages"][1]["content"], "How is cash flow?")
        self.assertEqual(result["reply"], "Cash flow looks steady.")

    def test_classify_signatures_parses_json_and_handles_fences(self) -> None:
        fake_response = json.dumps(
            {
                "model": "llama-32-3b-instruct",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                'Here is the classification:\n```json\n'
                                '{"0": "Salaries", "1": "Taxes", "2": "InvalidCat"}\n```'
                            ),
                        }
                    }
                ],
            }
        ).encode("utf-8")

        sigs = [
            {
                "key": "k0",
                "counterparty": "Payroll Co",
                "raw_type": "TRANSFER",
                "reference": "Oct",
                "direction": "expense",
            },
            {
                "key": "k1",
                "counterparty": "Agency Z",
                "raw_type": "DEBIT",
                "reference": "Q3",
                "direction": "expense",
            },
            {
                "key": "k2",
                "counterparty": "Bakery",
                "raw_type": "CARD",
                "reference": "Bread",
                "direction": "expense",
            },
        ]

        with patch.dict(
            os.environ,
            {"LLM_BASE_URL": "http://vllm.local:8080/v1", "LLM_MODEL": "llama-32-3b-instruct"},
            clear=False,
        ), patch("urllib.request.urlopen", return_value=FakeHTTPResponse(fake_response)):
            mapping = classify_signatures_with_llm(sigs)

        self.assertEqual(mapping, {"k0": "Salaries", "k1": "Taxes"})

    def test_context_and_explain_prompts_support_seasonal_and_dynamic_currency(self) -> None:
        txs = synthetic_ledger(date(2024, 1, 1), date(2025, 12, 31))
        store = make_store_from_txs(txs, opening_balance=10000.0)
        store.account_currency = "PLN"
        view = store.get_view(horizon_months=6)
        ctx = build_context_system_prompt(horizon_months=6, store=store)
        explain = build_explain_prompt(view)
        self.assertIn("SEASONAL MONTHLY FORECAST", ctx)
        self.assertIn("PLN ", ctx)
        self.assertIn("next 6 months", explain)
        self.assertIn("PLN ", explain)

    def test_context_prompt_grounds_dates(self) -> None:
        txs = synthetic_ledger(date(2024, 1, 1), date(2025, 12, 31))
        store = make_store_from_txs(txs, opening_balance=10000.0)
        ctx = build_context_system_prompt(horizon_months=6, store=store)
        self.assertIn(f"Today is {date.today().isoformat()}", ctx)
        self.assertIn("there is no actual data after 2025-12-31", ctx)
        self.assertIn("never invent due dates", ctx)
        forecast_block = ctx.split("Month by month", 1)[1].split("Planner notes", 1)[0]
        months = [
            line.split(":")[0].strip(" -")
            for line in forecast_block.splitlines()
            if line.startswith("  - ")
        ]
        self.assertEqual(months, ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"])


if __name__ == "__main__":
    unittest.main()
