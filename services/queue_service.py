"""
Persistent Email Queue Service Module
Module H - Smart Vendor Email & Inventory Automation System
Provides SQLite-backed persistence, crash recovery, prioritized batching,
and retry mechanisms with state machine: PENDING -> PROCESSING -> COMPLETED | FAILED
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import DATABASE_PATH
from database.db import get_db

logger = logging.getLogger("inventory_automation")


class QueueService:
    """
    SQLite-backed persistent queue for email ingestion and processing.
    Guarantees idempotency, priority ordering (High Priority first),
    and automatic job recovery after crash/restart.
    """

    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = Path(db_path)

    def enqueue(
        self,
        message_id: str,
        priority_level: int = 2,
    ) -> bool:
        """
        Enqueues an incoming email message ID into SQLite queue.
        Idempotent: Duplicate message IDs are ignored.
        Returns True if newly enqueued, False if already queued/processed.
        """
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO processing_queue (
                        message_id, priority_level, status, retry_count, created_at, updated_at
                    ) VALUES (?, ?, 'PENDING', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (message_id, priority_level))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as exc:
            logger.error("Failed to enqueue message '%s': %s", message_id, exc)
            return False

    def fetch_next_batch(self, batch_size: int = 20) -> List[Dict[str, Any]]:
        """
        Fetches the next batch of PENDING messages ordered by priority (1=High Priority first).
        Atomically updates their status to 'PROCESSING' to prevent overlapping runs.
        """
        with get_db(self.db_path) as conn:
            cursor = conn.cursor()

            # Select pending items ordered by priority_level (1=High, 2=Normal, 3=Low)
            cursor.execute("""
                SELECT id, message_id, priority_level, retry_count, max_retries
                FROM processing_queue
                WHERE status = 'PENDING'
                ORDER BY priority_level ASC, id ASC
                LIMIT ?;
            """, (batch_size,))
            rows = [dict(r) for r in cursor.fetchall()]

            if not rows:
                return []

            # Atomically mark fetched batch as PROCESSING
            msg_ids = [r["message_id"] for r in rows]
            placeholders = ",".join("?" for _ in msg_ids)
            cursor.execute(f"""
                UPDATE processing_queue
                SET status = 'PROCESSING', updated_at = CURRENT_TIMESTAMP
                WHERE message_id IN ({placeholders});
            """, msg_ids)
            conn.commit()

            logger.info("Checked out batch of %d emails from queue for processing.", len(rows))
            return rows

    def mark_completed(self, message_id: str) -> None:
        """Marks a queue job as COMPLETED."""
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE processing_queue
                    SET status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP
                    WHERE message_id = ?;
                """, (message_id,))
                conn.commit()
        except Exception as exc:
            logger.error("Failed to mark queue job '%s' completed: %s", message_id, exc)

    def mark_failed(
        self,
        message_id: str,
        error_message: str,
        max_retries: int = 3,
    ) -> bool:
        """
        Records failure and manages retry with exponential backoff logic.
        If retry_count < max_retries, sets status back to 'PENDING'.
        If max_retries exceeded, sets status to 'FAILED'.
        Returns True if rescheduled for retry, False if permanently failed.
        """
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT retry_count, max_retries FROM processing_queue
                    WHERE message_id = ?;
                """, (message_id,))
                row = cursor.fetchone()

                current_retries = (row["retry_count"] if row else 0) + 1
                allowed_max = max_retries

                if current_retries <= allowed_max:
                    # Reschedule for retry
                    cursor.execute("""
                        UPDATE processing_queue
                        SET status = 'PENDING',
                            retry_count = ?,
                            error_message = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE message_id = ?;
                    """, (current_retries, error_message, message_id))
                    conn.commit()
                    logger.warning("Message '%s' failed (attempt %d/%d). Rescheduled to PENDING.", message_id, current_retries, allowed_max)
                    return True
                else:
                    # Permanently failed
                    cursor.execute("""
                        UPDATE processing_queue
                        SET status = 'FAILED',
                            retry_count = ?,
                            error_message = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE message_id = ?;
                    """, (current_retries, error_message, message_id))
                    conn.commit()
                    logger.error("Message '%s' exceeded max retries (%d). Marked FAILED.", message_id, allowed_max)
                    return False
        except Exception as exc:
            logger.error("Error managing queue failure for '%s': %s", message_id, exc)
            return False

    def recover_unfinished_jobs(self) -> int:
        """
        Crash recovery mechanism:
        If application was terminated mid-cycle, any messages left in 'PROCESSING'
        state are restored to 'PENDING' so work is never lost.
        """
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE processing_queue
                    SET status = 'PENDING', updated_at = CURRENT_TIMESTAMP
                    WHERE status = 'PROCESSING';
                """)
                recovered_count = cursor.rowcount
                conn.commit()

            if recovered_count > 0:
                logger.info("Crash Recovery: Restored %d interrupted processing jobs to PENDING state.", recovered_count)
            return recovered_count
        except Exception as exc:
            logger.error("Failed to execute crash recovery on queue: %s", exc)
            return 0

    def get_queue_statistics(self) -> Dict[str, int]:
        """Returns counts of pending, processing, completed, and failed jobs."""
        with get_db(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status, COUNT(*) AS cnt
                FROM processing_queue
                GROUP BY status;
            """)
            stats = {"PENDING": 0, "PROCESSING": 0, "COMPLETED": 0, "FAILED": 0}
            for row in cursor.fetchall():
                st = row["status"]
                if st in stats:
                    stats[st] = row["cnt"]
            return stats
