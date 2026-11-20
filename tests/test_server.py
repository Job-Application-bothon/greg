import io
import json
import os
import unittest
from unittest.mock import patch

import greg


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


if __name__ == "__main__":
    unittest.main()
