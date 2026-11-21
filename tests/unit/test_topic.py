"""LLM topic gate tests."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from greg.llm.topic import is_on_topic

ENV = {"LLM_BASE_URL": "http://vllm.local:8080/v1", "LLM_MODEL": "m", "GREG_TOPIC_GUARD": ""}


def _history(*user_texts: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    for text in user_texts:
        msgs.append({"role": "user", "content": text})
        msgs.append({"role": "assistant", "content": "ok"})
    return msgs[:-1]


class TopicGateTest(unittest.TestCase):
    def test_no_reply_blocks_and_yes_allows(self) -> None:
        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", return_value={"reply": "NO", "model": "m"}
        ) as call:
            self.assertFalse(is_on_topic(_history("write me a poem")))
        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 3)
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertIn("write me a poem", call.call_args.args[0][1]["content"])

        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", return_value={"reply": " Yes.", "model": "m"}
        ):
            self.assertTrue(is_on_topic(_history("How much rent did I pay?")))

    def test_previous_user_turn_is_passed_for_follow_ups(self) -> None:
        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", return_value={"reply": "YES", "model": "m"}
        ) as call:
            is_on_topic(_history("What did I spend in March?", "and in April?"))
        content = call.call_args.args[0][1]["content"]
        self.assertIn("What did I spend in March?", content)
        self.assertIn("and in April?", content)

    def test_errors_and_disabled_gate_let_messages_through(self) -> None:
        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", side_effect=TimeoutError("slow")
        ):
            self.assertTrue(is_on_topic(_history("anything")))

        for env in ({**ENV, "GREG_TOPIC_GUARD": "false"}, {**ENV, "LLM_BASE_URL": ""}):
            with patch.dict(os.environ, env, clear=False), patch(
                "greg.llm.topic.call_llm"
            ) as call:
                self.assertTrue(is_on_topic(_history("write me a poem")))
                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
