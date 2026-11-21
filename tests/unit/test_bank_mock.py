"""Unit tests for mock connected-bank CSV discovery, labeling, and LedgerStore compatibility."""
from __future__ import annotations

import random
import unittest
from pathlib import Path

from greg.config import DEFAULT_BANK_MOCK_DIR
from greg.domain.bank_mock import (
    format_bank_account_label,
    list_mock_bank_files,
    pick_random_mock_bank_file,
)
from greg.domain.ledger import LedgerStore


class BankMockUnitTest(unittest.TestCase):
    def test_discovers_all_four_mock_csv_files_and_formats_labels(self) -> None:
        files = list_mock_bank_files(DEFAULT_BANK_MOCK_DIR)
        self.assertGreaterEqual(len(files), 4)
        self.assertEqual(format_bank_account_label("01_ice_cream_parlor.csv"), "Ice Cream Parlor")
        self.assertEqual(format_bank_account_label("06_garden_center_monthly.csv"), "Garden Center")
        self.assertEqual(format_bank_account_label("09_print_shop_declining.csv"), "Print Shop")
        self.assertEqual(
            format_bank_account_label("11_agri_supplier_weekly_DE_format.csv"),
            "Agri Supplier",
        )

    def test_pick_random_avoids_immediate_repeat(self) -> None:
        files = list_mock_bank_files(DEFAULT_BANK_MOCK_DIR)
        self.assertGreater(len(files), 1)
        rng = random.Random(42)
        first = files[0].name
        for _ in range(25):
            picked = pick_random_mock_bank_file(
                DEFAULT_BANK_MOCK_DIR,
                exclude_filename=first,
                rng=rng,
            )
            self.assertNotEqual(picked.name, first)

    def test_missing_directory_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            pick_random_mock_bank_file(Path("nonexistent_bank_mock_dir_xyz"))

    def test_all_bank_mock_csvs_import_and_forecast_cleanly(self) -> None:
        files = list_mock_bank_files(DEFAULT_BANK_MOCK_DIR)
        self.assertGreaterEqual(len(files), 4)
        for csv_path in files:
            store = LedgerStore()
            account_name = format_bank_account_label(csv_path.name)
            store.import_csv(
                csv_text=csv_path.read_text(encoding="utf-8-sig"),
                filename=csv_path.name,
                opening_balance=0.0,
                data_source="connected_bank",
                bank_account_name=account_name,
            )
            view = store.get_view()
            self.assertTrue(view["has_data"], msg=f"No data for {csv_path.name}")
            self.assertEqual(view["data_source"], "connected_bank")
            self.assertEqual(view["bank_account_name"], account_name)
            self.assertGreater(view["total_transactions"], 0)
            self.assertTrue(
                view["liquidity_agent"]["forecast_series"], msg=f"No forecast for {csv_path.name}"
            )


if __name__ == "__main__":
    unittest.main()
