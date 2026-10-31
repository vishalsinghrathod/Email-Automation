"""
Gmail Email Monitor Module
Module A - Smart Vendor Email & Inventory Automation System
Implements:
  - Real Gmail inbox monitoring & historyId incremental synchronization
  - Google Cloud Pub/Sub watch subscription setup & renewal logic
  - Resilient periodic backup polling reconciliation
  - Email classification, dual notification dispatch, and persistent queueing
"""

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config.settings import (
    DATABASE_PATH,
    GMAIL_SEARCH_KEYWORDS,
    REGISTERED_VENDOR_EMAILS,
)
from database.db import get_db
from services.email_classifier import EmailClassifier, PriorityLevel
from services.gmail_service import DemoInboxProvider, EmailMessage, GmailService
from services.notification_service import NotificationService
from services.queue_service import QueueService

logger = logging.getLogger("inventory_automation")


class EmailMonitor:
    """
    Monitors Gmail inbox continuously with event-driven sync & periodic polling fallback.
    Coordinates Classification (Module B), Notification (Module C), and Queueing (Module H).
    """

    def __init__(
        self,
        gmail_service: Optional[Any] = None,
        db_path: Path = DATABASE_PATH,
        classifier: Optional[EmailClassifier] = None,
        notification_service: Optional[NotificationService] = None,
        queue_service: Optional[QueueService] = None,
        pubsub_topic: Optional[str] = None,
    ):
        self.db_path = Path(db_path)
        self.gmail = gmail_service or GmailService()
        self.classifier = classifier or EmailClassifier()
        self.notification_service = notification_service or NotificationService(db_path=self.db_path)
        self.queue_service = queue_service or QueueService(db_path=self.db_path)
        self.pubsub_topic = pubsub_topic
        from services.database_service import DatabaseService
        self.db_service = DatabaseService(self.db_path)

    def get_state(self, key: str) -> Optional[str]:
        """Reads persistent system checkpoint state from SQLite."""
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM system_state WHERE key = ?;", (key,))
                row = cursor.fetchone()
                return row["value"] if row else None
        except Exception as exc:
            logger.debug("Failed to read system state for key '%s': %s", key, exc)
            return None

    def set_state(self, key: str, value: str) -> None:
        """Saves persistent system checkpoint state to SQLite."""
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO system_state (key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = CURRENT_TIMESTAMP;
                """, (key, value))
                conn.commit()
        except Exception as exc:
            logger.debug("Failed to write system state for key '%s': %s", key, exc)

    def renew_watch_subscription(self) -> bool:
        """
        Registers or renews Gmail push notification watch via Google Cloud Pub/Sub.
        Google Gmail watch subscriptions expire every 7 days and must be renewed.
        """
        if not self.pubsub_topic:
            logger.debug("No Pub/Sub topic configured. Operating in resilient polling mode.")
            return False

        client = getattr(self.gmail, "service", None)
        if not client:
            return False

        try:
            request_body = {
                "labelIds": ["INBOX"],
                "topicName": self.pubsub_topic,
            }
            res = client.users().watch(userId="me", body=request_body).execute()
            history_id = res.get("historyId")
            expiration = res.get("expiration")

            if history_id:
                self.set_state("gmail_last_history_id", str(history_id))
            if expiration:
                self.set_state("gmail_watch_expiration", str(expiration))

            logger.info("Gmail watch subscription renewed successfully (Expires: %s, historyId: %s)", expiration, history_id)
            return True
        except Exception as exc:
            logger.warning("Failed to renew Gmail watch subscription: %s. Continuing with backup polling.", exc)
            return False

    def sync_history(self) -> List[str]:
        """
        Performs incremental synchronization using Gmail historyId.
        Returns list of newly detected message IDs since last sync checkpoint.
        """
        last_history_id = self.get_state("gmail_last_history_id")
        client = getattr(self.gmail, "service", None)

        if not client or not last_history_id:
            return []

        new_msg_ids: List[str] = []
        try:
            res = client.users().history().list(
                userId="me",
                startHistoryId=last_history_id,
                historyTypes=["messageAdded"],
            ).execute()

            for record in res.get("history", []):
                for msg_added in record.get("messagesAdded", []):
                    msg = msg_added.get("message", {})
                    if msg.get("id"):
                        new_msg_ids.append(msg["id"])

            latest_history = res.get("historyId")
            if latest_history:
                self.set_state("gmail_last_history_id", str(latest_history))

            logger.debug("Gmail history sync discovered %d new messages.", len(new_msg_ids))
        except Exception as exc:
            logger.warning("Gmail history sync failed (%s). Falling back to full reconciliation.", exc)

        return new_msg_ids

    def process_incoming_email_record(self, email_msg: EmailMessage) -> Dict[str, Any]:
        """
        Processes a single newly detected email:
        1. Classifies priority (High Priority, Normal, Promotional, Review)
        2. Sends desktop & email notifications if actionable
        3. Persists email metadata in 'emails' table
        4. Enqueues message ID into SQLite processing queue
        """
        msg_id = email_msg.message_id
        sender_email = email_msg.sender_email
        sender_name = email_msg.sender_name
        subject = email_msg.subject
        has_attachments = any(p.get("filename") for p in email_msg.raw_parts)

        # 1. Classification
        body_text_val = getattr(email_msg, "body_snippet", "") or ""
        classification = self.classifier.classify(
            sender_email=sender_email,
            subject=subject,
            body_text=body_text_val,
            has_attachments=has_attachments,
        )

        logger.info(
            "Classified email '%s' as [%s] (Rank %d): %s",
            msg_id,
            classification.category.value,
            classification.priority_rank,
            classification.reason,
        )

        # 2. Dual Notifications (Desktop + Email) for Actionable / High Priority
        notification_results = {"desktop": False, "email": False}
        if classification.is_high_priority:
            client = getattr(self.gmail, "service", None)
            notification_results = self.notification_service.notify_all(
                sender_name=sender_name,
                sender_email=sender_email,
                subject=subject,
                priority=classification.category.value,
                received_at=email_msg.received_at,
                message_id=msg_id,
                gmail_client=client,
            )

        # 3. Store email history in SQLite 'emails' table
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO emails (
                        message_id, thread_id, sender_name, sender_email, subject,
                        body_preview, received_at, priority, status, attachment_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        body_preview = CASE WHEN excluded.body_preview != '' THEN excluded.body_preview ELSE emails.body_preview END,
                        priority = excluded.priority,
                        attachment_count = excluded.attachment_count;
                """, (
                    msg_id,
                    email_msg.thread_id,
                    sender_name,
                    sender_email,
                    subject,
                    body_text_val[:4000],
                    email_msg.received_at,
                    classification.category.value,
                    len([p for p in email_msg.raw_parts if p.get("filename")]),
                ))
                conn.commit()
        except Exception as exc:
            logger.warning("Failed to record email in database table: %s", exc)

        # 4. Enqueue into persistent SQLite queue
        self.queue_service.enqueue(
            message_id=msg_id,
            priority_level=classification.priority_rank,
        )

        # 5. Record telemetry in monitored_accounts table
        try:
            self.db_service.record_monitored_stats(
                email_address=sender_email,
                is_important=classification.is_high_priority,
            )
        except Exception as exc:
            logger.debug("Telemetry record ignored for monitored account '%s': %s", sender_email, exc)

        return {
            "message_id": msg_id,
            "classification": classification.to_dict(),
            "notifications": notification_results,
        }

    def run_reconciliation_cycle(
        self,
        vendor_emails: Optional[Set[str]] = None,
        keywords: Optional[List[str]] = None,
        max_results: int = 15,
    ) -> List[EmailMessage]:
        """
        Full inbox reconciliation cycle (backup polling / scheduled monitor pass).
        Fetches unread emails matching inventory criteria and routes them into the pipeline.
        """
        logger.info("Executing inbox reconciliation monitoring cycle...")

        # Dynamically load all active monitored accounts linked via UI
        combined_vendor_emails: Set[str] = set()
        if vendor_emails:
            combined_vendor_emails.update(v.lower() for v in vendor_emails)

        try:
            monitored_list = self.db_service.get_monitored_accounts()
            active_emails = {acc["email_address"].lower() for acc in monitored_list if acc.get("is_active", 1)}
            combined_vendor_emails.update(active_emails)
        except Exception as exc:
            logger.debug("Could not fetch monitored accounts list: %s", exc)

        try:
            emails = self.gmail.fetch_unread_inventory_emails(
                vendor_emails=combined_vendor_emails or None,
                keywords=keywords,
                max_results=max_results,
            )
        except Exception as exc:
            logger.error("Inbox monitoring cycle failed: %s", exc)
            return []

        for email_msg in emails:
            self.process_incoming_email_record(email_msg)

        logger.info("Monitoring cycle completed. %d emails inspected and queued.", len(emails))
        return emails
