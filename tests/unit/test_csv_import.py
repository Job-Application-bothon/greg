"""Unit tests for CSV delimiter/date/amount parsing, column mapping, and FX logic."""
from __future__ import annotations

import unittest
from datetime import date

from greg.domain.csv_import import (
    detect_delimiter,
    infer_currency_from_headers,
    parse_amount,
    parse_csv_transactions,
    parse_date,
)
from greg.domain.ledger import LedgerStore


class CsvImportTest(unittest.TestCase):
    def test_date_amount_delimiter_and_currency_helpers(self) -> None:
        self.assertEqual(detect_delimiter("a;b;c"), ";")
        self.assertEqual(detect_delimiter("a,b,c"), ",")
        self.assertEqual(parse_date("2025-03"), date(2025, 3, 1))
        self.assertEqual(parse_date("01.02.25"), date(2025, 2, 1))
        self.assertEqual(parse_date("2025-01-01 00:00:00"), date(2025, 1, 1))
        self.assertAlmostEqual(parse_amount("1.234,56 €"), 1234.56)
        self.assertAlmostEqual(parse_amount("1,234.56"), 1234.56)
        self.assertEqual(infer_currency_from_headers("revenue_pln", "expenses_pln"), "PLN")
        self.assertIsNone(infer_currency_from_headers("amount", "date"))

    def test_split_columns_and_validation_errors(self) -> None:
        csv_text = "date,revenue_pln,expenses_pln\n2025-01,10000,4500\n2025-02,12000,-5000\n"
        txs, currency = parse_csv_transactions(
            csv_text,
            column_map={
                "date": "date",
                "amount_mode": "split",
                "income": "revenue_pln",
                "expenses": "expenses_pln",
            },
        )
        self.assertEqual(currency, "PLN")
        self.assertEqual(len(txs), 4)
        self.assertEqual(txs[1]["amount"], -4500.0)
        self.assertEqual(txs[3]["amount"], -5000.0)

        with self.assertRaises(ValueError):
            parse_csv_transactions(csv_text, column_map={"date": "missing_col", "amount_mode": "split"})

    def test_hybrid_llm_classifier_and_social_security_guardrail(self) -> None:
        csv_text = (
            "Date,Type,Counterparty,Description,Amount,Currency\n"
            "2026-01-10,TRANSFER,Unknown Payroll Provider,Monthly Transfer,-3000.00,EUR\n"
            "2026-01-15,DIRECT DEBIT,Techniker Krankenkasse Social Health Fund,"
            "Health Insurance,-900.00,EUR\n"
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
                    # Even if LLM mislabels social security as Taxes, guardrail keeps it in Other.
                    out[s["key"]] = "Taxes"
            return out

        store = LedgerStore()
        store.import_csv(csv_text, llm_classifier=fake_llm_classifier)
        jan = store.get_view(selected_month="2026-01")
        cat_totals = {c["category"]: c["total"] for c in jan["expense_breakdown"]}
        self.assertEqual(cat_totals["Salaries"], 3000.0)
        self.assertEqual(cat_totals["Taxes"], 600.0)
        self.assertEqual(cat_totals["Other"], 900.0)


if __name__ == "__main__":
    unittest.main()
