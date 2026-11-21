"""Unit tests for deterministic keyword categorization and social-security guardrails."""
from __future__ import annotations

import unittest

from greg.domain.categories import (
    classify_category_keywords,
    is_social_security,
    normalize_category,
)


class CategoriesTest(unittest.TestCase):
    def test_keyword_classification_and_social_security_guardrail(self) -> None:
        self.assertEqual(
            classify_category_keywords("TRANSFER", "Finanzamt Frankfurt", "Wage Tax Q1"),
            "Taxes",
        )
        self.assertEqual(
            classify_category_keywords("SALARY", "Alice Smith", "January Wages"),
            "Salaries",
        )
        self.assertEqual(
            classify_category_keywords(
                "DIRECT DEBIT", "Techniker Krankenkasse Social Health Fund", "Health Insurance"
            ),
            "Other",
        )
        self.assertTrue(
            is_social_security("DEBIT", "Techniker Krankenkasse", "Sozialversicherung")
        )
        self.assertFalse(is_social_security("TAX", "Finanzamt", "Income Tax"))

    def test_normalize_category(self) -> None:
        self.assertEqual(normalize_category("salary"), "Salaries")
        self.assertEqual(normalize_category("Taxes"), "Taxes")
        self.assertEqual(normalize_category("other"), "Other")
        self.assertEqual(normalize_category("Unknown", "SALARY", "Bob", ""), "Salaries")


if __name__ == "__main__":
    unittest.main()
