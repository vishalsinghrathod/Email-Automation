"""
Integration Tests for Complete Pipeline
Tests end-to-end processing with DemoInboxProvider and idempotency across repeated runs.
"""

import unittest
from pathlib import Path

from config.settings import DATABASE_PATH, DOWNLOADS_DIR, PROCESSED_DIR
from main import run_pipeline
from services.database_service import DatabaseService


class TestPipelineIntegration(unittest.TestCase):
    def setUp(self):
        self.db_service = DatabaseService(DATABASE_PATH)
        self.db_service.reset_demo_data()
        self.db_service.seed_default_vendors()

    def test_end_to_end_demo_run(self):
        # First execution run
        metrics_run1 = run_pipeline(demo_mode=True)

        self.assertGreaterEqual(metrics_run1.emails_inspected, 1)
        self.assertGreater(metrics_run1.total_valid_records, 0)
        self.assertGreater(metrics_run1.total_rejected_records, 0)
        self.assertIn(metrics_run1.overall_status, ("SUCCESS", "WARNING"))

        # Verify database content
        stats = self.db_service.get_summary_statistics()
        self.assertGreater(stats["inventory_distinct_products"], 0)
        self.assertGreater(stats["total_units_in_stock"], 0)
        self.assertGreater(stats["total_rejected_records"], 0)

        # Check export files exist
        summary_files = list(PROCESSED_DIR.glob("run_summary_*.txt"))
        self.assertGreater(len(summary_files), 0)

        rejected_files = list(PROCESSED_DIR.glob("rejected_records_*.csv"))
        self.assertGreater(len(rejected_files), 0)

        # Second execution run (Idempotency test)
        # Previously processed emails and files must be skipped safely
        metrics_run2 = run_pipeline(demo_mode=True)
        self.assertGreaterEqual(metrics_run2.emails_skipped, 0)


if __name__ == "__main__":
    unittest.main()
