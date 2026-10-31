"""
Unit Tests for InventoryProcessor
Tests header normalization, field cleaning, type conversions, and validation.
"""

import unittest
from pathlib import Path
import tempfile
import pandas as pd

from services.inventory_processor import InventoryProcessor


class TestInventoryProcessor(unittest.TestCase):
    def setUp(self):
        self.processor = InventoryProcessor()
        self.test_data_dir = Path(__file__).resolve().parent / "data"

    def test_clean_price(self):
        # Valid prices
        p1, err1 = self.processor.clean_price("$145.50")
        self.assertIsNone(err1)
        self.assertEqual(p1, 145.50)

        p2, err2 = self.processor.clean_price("0.99")
        self.assertIsNone(err2)
        self.assertEqual(p2, 0.99)

        p3, err3 = self.processor.clean_price("1,250.00")
        self.assertIsNone(err3)
        self.assertEqual(p3, 1250.00)

        # Invalid prices
        _, err_neg = self.processor.clean_price("-50.00")
        self.assertIn("NEGATIVE_PRICE", err_neg)

        _, err_bad = self.processor.clean_price("N/A")
        self.assertIn("INVALID_PRICE_FORMAT", err_bad)

        _, err_null = self.processor.clean_price(None)
        self.assertIn("PRICE_IS_NULL", err_null)

    def test_clean_quantity(self):
        # Valid quantities
        q1, err1 = self.processor.clean_quantity("150")
        self.assertIsNone(err1)
        self.assertEqual(q1, 150)

        q2, err2 = self.processor.clean_quantity("10.0")
        self.assertIsNone(err2)
        self.assertEqual(q2, 10)

        q3, err3 = self.processor.clean_quantity("0")
        self.assertIsNone(err3)
        self.assertEqual(q3, 0)

        # Invalid quantities
        _, err_neg = self.processor.clean_quantity("-15")
        self.assertIn("NEGATIVE_QUANTITY", err_neg)

        _, err_frac = self.processor.clean_quantity("15.75")
        self.assertIn("FRACTIONAL_QUANTITY", err_frac)

        _, err_text = self.processor.clean_quantity("ten")
        self.assertIn("INVALID_QUANTITY_FORMAT", err_text)

    def test_clean_date(self):
        # Standard ISO
        d1, err1 = self.processor.clean_date("2026-10-22")
        self.assertIsNone(err1)
        self.assertEqual(d1, "2026-10-22")

        # Slash format
        d2, err2 = self.processor.clean_date("10/22/2026")
        self.assertIsNone(err2)
        self.assertEqual(d2, "2026-10-22")

        # Invalid date
        _, err_bad = self.processor.clean_date("invalid_date_string")
        self.assertIn("INVALID_DATE_FORMAT", err_bad)

    def test_process_valid_file(self):
        valid_file = self.test_data_dir / "valid_inventory_apex.csv"
        result = self.processor.process_file(valid_file)
        self.assertTrue(result.success)
        self.assertEqual(result.valid_count, 5)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(len(result.valid_records), 5)
        self.assertEqual(result.valid_records[0]["vendor_name"], "Apex Global Supplies")
        self.assertEqual(result.valid_records[0]["product_id"], "APX-101")
        self.assertEqual(result.valid_records[0]["price"], 0.85)

    def test_process_mixed_file(self):
        mixed_file = self.test_data_dir / "mixed_inventory_nexus.csv"
        result = self.processor.process_file(mixed_file)
        self.assertTrue(result.success)
        # Expected:
        # NEX-201, NEX-202, NEX-203 are valid (3)
        # missing product_id rejected
        # NEX-204 negative qty rejected
        # NEX-205 negative price rejected
        # second NEX-201 duplicate row rejected
        self.assertEqual(result.valid_count, 3)
        self.assertEqual(result.rejected_count, 4)

    def test_missing_required_columns(self):
        missing_file = self.test_data_dir / "missing_columns_prime.csv"
        result = self.processor.process_file(missing_file)
        self.assertFalse(result.success)
        self.assertIn("Missing required canonical columns", result.error_message)
        self.assertIn("sku", result.error_message)

    def test_empty_file_handling(self):
        empty_file = self.test_data_dir / "empty_file.csv"
        result = self.processor.process_file(empty_file)
        self.assertFalse(result.success)
        self.assertIn("empty", result.error_message.lower())


if __name__ == "__main__":
    unittest.main()
