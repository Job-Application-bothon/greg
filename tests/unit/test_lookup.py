"""Date extraction and DATE LOOKUP prompt section tests."""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from greg.llm.lookup import build_date_lookup, extract_dates
from tests.helpers import make_store_from_txs, synthetic_ledger

D = date(2025, 3, 14)
PHRASINGS = [
    "What happened on 2025-03-14?",
    "what did I pay on 14.03.2025",
    "balance on 14.03.25?",
    "payments on 14 March 2025",
    "payments on March 14, 2025",
    "what came in on the 14th of March",
    "spending Mar 14",
    "Zahlungen am 14. März 2025",
]


class ExtractDatesTest(unittest.TestCase):
    def test_day_phrasings(self) -> None:
        for q in PHRASINGS:
            with self.subTest(q=q):
                days, months = extract_dates(q, [2025])
                self.assertEqual(days, [D])
                self.assertEqual(months, [])

    def test_month_phrasings(self) -> None:
        for q in ("How much did I spend in May?", "income May 2025", "show 2025-05",
                  "costs 05/2025", "Ausgaben im Mai"):
            with self.subTest(q=q):
                self.assertEqual(extract_dates(q, [2025]), ([], ["2025-05"]))

    def test_ambiguous_words_and_plain_numbers_are_ignored(self) -> None:
        for q in ("May I ask how to import a CSV?", "May we talk?", "I paid 2025 euros",
                  "What can you do?"):
            with self.subTest(q=q):
                self.assertEqual(extract_dates(q, [2025]), ([], []))

    def test_missing_year_expands_to_data_years_and_is_capped(self) -> None:
        days, _ = extract_dates("on 14 March", [2024, 2025])
        self.assertEqual(days, [date(2024, 3, 14), date(2025, 3, 14)])
        many = " and ".join(f"{d} March 2025" for d in range(1, 10))
        self.assertEqual(len(extract_dates(many, [2025])[0]), 4)


class DateLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = make_store_from_txs(
            synthetic_ledger(date(2025, 1, 1), date(2025, 12, 31)), opening_balance=5000.0
        )
        self.balance: dict[date, float] = {}
        running = 5000.0
        by_day: dict[date, list] = {}
        for t in self.store.transactions:
            by_day.setdefault(t["date_obj"], []).append(t)
        d = date(2025, 1, 1)
        while d <= date(2025, 12, 31):
            running += sum(t["amount"] for t in by_day.get(d, []))
            self.balance[d] = round(running, 2)
            d += timedelta(days=1)
        self.by_day = by_day

    def test_every_day_of_the_year_reports_exact_balance_and_payments(self) -> None:
        d = date(2025, 1, 1)
        while d <= date(2025, 12, 31):
            out = build_date_lookup(self.store, f"What happened on {d.isoformat()}?")
            self.assertIn(d.isoformat(), out)
            self.assertIn(f"{self.balance[d]:,.2f}", out, msg=str(d))
            for t in sorted(self.by_day.get(d, []), key=lambda t: -abs(t["amount"]))[:12]:
                self.assertIn(f"{abs(t['amount']):,.2f}", out, msg=str(d))
            d += timedelta(days=1)

    def test_every_month_reports_exact_expenses(self) -> None:
        for mo in range(1, 13):
            name = date(2025, mo, 1).strftime("%B")
            out = build_date_lookup(self.store, f"How much did I spend in {name} 2025?")
            exp = sum(
                -t["amount"] for t in self.store.transactions
                if t["date_obj"].month == mo and t["amount"] < 0
            )
            self.assertIn(f"expenses €{exp:,.2f}", out, msg=name)

    def test_dates_outside_the_data_say_so(self) -> None:
        out = build_date_lookup(self.store, "What did I pay on 3 March 2027 and in June 2026?")
        self.assertIn("2027-03-03", out)
        self.assertIn("no bank data for this date", out)
        self.assertIn("no bank data for this month", out)

    def test_no_dates_or_no_data_adds_nothing(self) -> None:
        self.assertEqual(build_date_lookup(self.store, "How do I import a CSV?"), "")
        empty = make_store_from_txs([], opening_balance=0.0)
        self.assertEqual(build_date_lookup(empty, "on 2025-03-14"), "")


if __name__ == "__main__":
    unittest.main()
