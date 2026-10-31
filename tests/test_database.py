"""
Unit Tests for DatabaseService
Tests SQLite schema creation, vendor registration, email logging,
inventory upsert idempotency, and audit tables.
"""

import tempfile
import unittest
from pathlib import Path

from services.database_service import DatabaseService


class TestDatabaseService(unittest.TestCase):
    def setUp(self):
        # Create a temporary database for test isolation
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_inventory.db"
        self.db = DatabaseService(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_vendor_registration_and_retrieval(self):
        vid = self.db.register_vendor("Test Vendor LLC", "orders@testvendor.com", "VEND-TEST")
        self.assertGreater(vid, 0)

        vendor = self.db.get_vendor_by_email("orders@testvendor.com")
        self.assertIsNotNone(vendor)
        self.assertEqual(vendor["name"], "Test Vendor LLC")
        self.assertEqual(vendor["vendor_code"], "VEND-TEST")

    def test_email_idempotency(self):
        msg_id = "test_msg_9999"
        self.assertFalse(self.db.is_email_processed(msg_id))

        self.db.log_email(
            message_id=msg_id,
            sender_email="orders@testvendor.com",
            subject="Inventory Update",
            received_at=None,
            attachment_count=1,
            status="SUCCESS",
        )

        self.assertTrue(self.db.is_email_processed(msg_id))

    def test_file_checksum_deduplication(self):
        dummy_hash = "abc123def4567890"
        self.assertFalse(self.db.is_file_already_processed(dummy_hash))

        self.db.record_processed_file(dummy_hash, "test_file.csv", 1024)
        self.assertTrue(self.db.is_file_already_processed(dummy_hash))

    def test_inventory_upsert(self):
        records_batch1 = [{
            "vendor_name": "Acme Tools",
            "product_id": "ACM-001",
            "product_name": "Hammer 16oz",
            "sku": "SKU-HAM-16",
            "quantity": 100,
            "price": 15.99,
            "updated_date": "2026-10-20",
        }]

        # Initial insert
        success, errors = self.db.upsert_inventory_records(records_batch1, "batch1.csv")
        self.assertEqual(success, 1)
        self.assertEqual(errors, 0)

        items = self.db.get_latest_inventory()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], 100)
        self.assertEqual(items[0]["price"], 15.99)

        # Update existing record with new quantity and price
        records_batch2 = [{
            "vendor_name": "Acme Tools",
            "product_id": "ACM-001",
            "product_name": "Hammer 16oz Pro",
            "sku": "SKU-HAM-16",
            "quantity": 85,
            "price": 16.50,
            "updated_date": "2026-10-22",
        }]

        success2, errors2 = self.db.upsert_inventory_records(records_batch2, "batch2.csv")
        self.assertEqual(success2, 1)
        self.assertEqual(errors2, 0)

        # Should still have only 1 row, but with updated values!
        items_updated = self.db.get_latest_inventory()
        self.assertEqual(len(items_updated), 1)
        self.assertEqual(items_updated[0]["quantity"], 85)
        self.assertEqual(items_updated[0]["price"], 16.50)
        self.assertEqual(items_updated[0]["product_name"], "Hammer 16oz Pro")

    def test_rejected_records_logging(self):
        rejected_rows = [{
            "vendor_name": "Bad Vendor",
            "product_id": "",
            "raw_data": "raw row string",
            "rejection_reason": "MISSING_PRODUCT_ID",
        }]

        count = self.db.record_rejected_records(rejected_rows, "bad_file.csv")
        self.assertEqual(count, 1)

        stats = self.db.get_summary_statistics()
        self.assertEqual(stats["total_rejected_records"], 1)


if __name__ == "__main__":
    unittest.main()
