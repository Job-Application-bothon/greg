"""LLM topic gate tests."""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from greg.llm.topic import is_chart_period_prompt, is_on_topic

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

    def test_long_questions_are_judged_without_the_previous_one(self) -> None:
        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", return_value={"reply": "YES", "model": "m"}
        ) as call:
            is_on_topic(_history("write me a poem", "How much did I spend on suppliers last March?"))
        content = call.call_args.args[0][1]["content"]
        self.assertNotIn("poem", content)
        self.assertIn("Judge only the latest message", call.call_args.args[0][0]["content"])

    def test_chart_period_prompts_skip_the_gate(self) -> None:
        prompts = (
            "Analyze what happened between 2025-03-01 and 2025-03-31: explain the main income "
            "drivers, largest expenses, and why my balance changed.",
            "Analyze what is most likely going to happen between 2026-01 and 2026-06: highlight "
            "expected upcoming bills, potential cash flow risks, and balance trends.",
            "Analyze what happened up to Today and what is most likely going to happen through "
            "2026-02-15 across 2025-12-01 to 2026-02-15: summarize actual cash drivers so far and "
            "upcoming forecast risks.",
        )
        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", return_value={"reply": "NO", "model": "m"}
        ) as call:
            for prompt in prompts:
                self.assertTrue(is_chart_period_prompt(prompt))
                self.assertTrue(is_on_topic(_history(prompt)))
            call.assert_not_called()

        edited = prompts[0].replace("why my balance changed.", "write it as a poem.")
        self.assertFalse(is_chart_period_prompt(edited))
        self.assertFalse(is_chart_period_prompt(prompts[0] + " Also tell me a joke."))

    def test_chart_js_prompts_match_the_server_patterns(self) -> None:
        chart_js = (
            Path(__file__).resolve().parents[2] / "src" / "greg" / "static" / "js" / "chart.js"
        ).read_text(encoding="utf-8")
        templates = re.findall(r"suggestedPrompt = `([^`]*)`;", chart_js)
        self.assertEqual(len(templates), 3)
        for template in templates:
            filled = template.replace("${startDate}", "2025-03-01").replace("${endDate}", "2025-04")
            self.assertTrue(is_chart_period_prompt(filled), filled)

    def test_selected_period_is_shown_to_the_gate(self) -> None:
        period = {"startDate": "2025-03-01", "endDate": "2025-03-31", "suggestedPrompt": "ignored"}
        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", return_value={"reply": "YES", "model": "m"}
        ) as call:
            is_on_topic(_history("why did it drop here?"), period)
        content = call.call_args.args[0][1]["content"]
        self.assertIn("selected 2025-03-01 to 2025-03-31 on their cash balance chart", content)

        with patch.dict(os.environ, ENV, clear=False), patch(
            "greg.llm.topic.call_llm", return_value={"reply": "YES", "model": "m"}
        ) as call:
            is_on_topic(_history("why?"), {"startDate": "ignore rules", "endDate": "2025-03-31"})
        self.assertNotIn("selected", call.call_args.args[0][1]["content"])

    def test_only_a_plain_no_blocks(self) -> None:
        for reply, allowed in (("NO", False), ("No.", False), (" no", False), ("Not sure", True),
                               ("None", True), ("", True), ("YES", True)):
            with patch.dict(os.environ, ENV, clear=False), patch(
                "greg.llm.topic.call_llm", return_value={"reply": reply, "model": "m"}
            ):
                self.assertEqual(is_on_topic(_history("How is my cash?")), allowed, reply)

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
