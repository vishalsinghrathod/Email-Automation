"""
Unit Tests for QueueService Module
Tests SQLite-backed persistence, priority checkout, retry logic, and crash recovery.
"""

import tempfile
import unittest
from pathlib import Path

from database.db import init_database
from services.queue_service import QueueService


class TestQueueService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_queue.db"
        init_database(self.db_path)
        self.queue = QueueService(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_enqueue_and_idempotency(self):
        # First enqueue
        res1 = self.queue.enqueue("msg_001", priority_level=1)
        self.assertTrue(res1)

        # Duplicate enqueue should be ignored
        res2 = self.queue.enqueue("msg_001", priority_level=1)
        self.assertFalse(res2)

        stats = self.queue.get_queue_statistics()
        self.assertEqual(stats["PENDING"], 1)

    def test_priority_ordering(self):
        # Enqueue Normal (2), then Low (3), then High (1)
        self.queue.enqueue("normal_msg", priority_level=2)
        self.queue.enqueue("low_msg", priority_level=3)
        self.queue.enqueue("high_msg", priority_level=1)

        batch = self.queue.fetch_next_batch(batch_size=10)
        self.assertEqual(len(batch), 3)

        # High priority must be first
        self.assertEqual(batch[0]["message_id"], "high_msg")
        self.assertEqual(batch[0]["priority_level"], 1)

        # Normal priority must be second
        self.assertEqual(batch[1]["message_id"], "normal_msg")

        # Low priority must be third
        self.assertEqual(batch[2]["message_id"], "low_msg")

    def test_mark_completed(self):
        self.queue.enqueue("msg_done", priority_level=1)
        self.queue.fetch_next_batch(batch_size=1)
        self.queue.mark_completed("msg_done")

        stats = self.queue.get_queue_statistics()
        self.assertEqual(stats["COMPLETED"], 1)
        self.assertEqual(stats["PENDING"], 0)

    def test_mark_failed_with_retry(self):
        self.queue.enqueue("msg_flaky", priority_level=1)
        self.queue.fetch_next_batch(batch_size=1)

        # First failure -> reschedules as PENDING
        retryable = self.queue.mark_failed("msg_flaky", error_message="Network glitch", max_retries=2)
        self.assertTrue(retryable)

        stats = self.queue.get_queue_statistics()
        self.assertEqual(stats["PENDING"], 1)

        # Second failure -> reschedules as PENDING
        self.queue.fetch_next_batch(batch_size=1)
        retryable2 = self.queue.mark_failed("msg_flaky", error_message="Network glitch", max_retries=2)
        self.assertTrue(retryable2)

        # Third failure -> exceeds max_retries, marked FAILED
        self.queue.fetch_next_batch(batch_size=1)
        retryable3 = self.queue.mark_failed("msg_flaky", error_message="Fatal error", max_retries=2)
        self.assertFalse(retryable3)

        stats_after = self.queue.get_queue_statistics()
        self.assertEqual(stats_after["FAILED"], 1)

    def test_crash_recovery(self):
        self.queue.enqueue("interrupted_job", priority_level=1)
        # Checked out to PROCESSING
        self.queue.fetch_next_batch(batch_size=1)

        stats_before = self.queue.get_queue_statistics()
        self.assertEqual(stats_before["PROCESSING"], 1)
        self.assertEqual(stats_before["PENDING"], 0)

        # Simulate application restart / crash recovery
        recovered = self.queue.recover_unfinished_jobs()
        self.assertEqual(recovered, 1)

        stats_after = self.queue.get_queue_statistics()
        self.assertEqual(stats_after["PENDING"], 1)
        self.assertEqual(stats_after["PROCESSING"], 0)


if __name__ == "__main__":
    unittest.main()

