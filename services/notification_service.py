"""
Dual Notification Service Module
Module C - Smart Vendor Email & Inventory Automation System
Implements:
  1. Desktop Notifications using Plyer (Windows Toasts)
  2. Email Alerts using Gmail API (OAuth 2.0 authenticated)
Ensures full fault isolation: failure in any notification method
will never block or interrupt the core inventory processing pipeline.
"""

import base64
import email
from email.mime.text import MIMEText
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

from config.settings import (
    DATABASE_PATH,
    ENABLE_DESKTOP_NOTIFICATIONS,
    ENABLE_EMAIL_NOTIFICATIONS,
    NOTIFICATION_ALERT_EMAIL,
)
from database.db import get_db

logger = logging.getLogger("inventory_automation")

# Gracefully silence background thread Win32 notification errors in headless/restricted console environments
def _win32_thread_excepthook(args):
    exc_str = str(args.exc_value) if args.exc_value else ""
    if "Shell_NotifyIconW" in exc_str or "balloontip" in exc_str:
        logger.debug("Win32 notification tray notification error safely intercepted: %s", exc_str)
    else:
        sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

if hasattr(threading, "excepthook"):
    threading.excepthook = _win32_thread_excepthook


class NotificationService:
    """
    Manages dual-channel alerts (Desktop Toast + Outgoing Email Alert)
    with idempotency, audit logging in SQLite, and complete fault tolerance.
    """

    def __init__(
        self,
        alert_email: Optional[str] = None,
        enable_desktop: bool = ENABLE_DESKTOP_NOTIFICATIONS,
        enable_email: bool = ENABLE_EMAIL_NOTIFICATIONS,
        db_path: Path = DATABASE_PATH,
    ):
        self.alert_email = alert_email if alert_email is not None else NOTIFICATION_ALERT_EMAIL
        self.enable_desktop = enable_desktop
        self.enable_email = enable_email
        self.db_path = Path(db_path)
        self.notified_message_ids: Set[str] = set()

    def has_notified(self, message_id: str, notification_type: str) -> bool:
        """Checks if a notification was already logged for this message ID."""
        cache_key = f"{notification_type}:{message_id}"
        if cache_key in self.notified_message_ids:
            return True

        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 1 FROM notifications 
                    WHERE message_id = ? AND notification_type = ? AND status = 'SENT'
                    LIMIT 1;
                """, (message_id, notification_type))
                if cursor.fetchone():
                    self.notified_message_ids.add(cache_key)
                    return True
        except Exception as exc:
            logger.debug("Failed to check notification history: %s", exc)
        return False

    def log_notification(
        self,
        message_id: str,
        notification_type: str,
        recipient: Optional[str],
        subject: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Persists notification delivery attempt to SQLite."""
        try:
            with get_db(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO notifications (
                        message_id, notification_type, recipient, subject, status, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?);
                """, (message_id, notification_type, recipient or "Local Display", subject, status, error_message))
                conn.commit()
            if status == "SENT":
                self.notified_message_ids.add(f"{notification_type}:{message_id}")
        except Exception as exc:
            logger.warning("Failed to record notification log in database: %s", exc)

    def send_desktop_notification(
        self,
        sender_name: str,
        subject: str,
        priority: str = "Normal",
        message_id: Optional[str] = None,
    ) -> bool:
        """
        Sends a native Windows desktop notification via Plyer.
        Gracefully handles environments where desktop notifications are unavailable.
        """
        if not self.enable_desktop:
            logger.debug("Desktop notifications are disabled by configuration.")
            return False

        if message_id and self.has_notified(message_id, "DESKTOP"):
            logger.debug("Desktop notification already delivered for email '%s'", message_id)
            return True

        title = f"[{priority.upper()}] New Vendor Email" if "high" in priority.lower() else "New Vendor Email"
        message_body = f"From: {sender_name}\nSubject: {subject[:75]}"

        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message_body,
                app_name="Vendor Inventory Automation",
                timeout=8,
            )
            logger.info("Delivered Desktop notification for '%s'", subject[:40])
            if message_id:
                self.log_notification(message_id, "DESKTOP", "Desktop GUI", subject, "SENT")
            return True
        except Exception as exc:
            logger.warning("Desktop notification delivery failed (safe fallback): %s", exc)
            if message_id:
                self.log_notification(message_id, "DESKTOP", "Desktop GUI", subject, "FAILED", str(exc))
            return False

    def send_email_notification(
        self,
        sender_email: str,
        subject: str,
        priority: str = "Normal",
        received_at: Optional[datetime] = None,
        message_id: Optional[str] = None,
        gmail_client: Optional[Any] = None,
    ) -> bool:
        """
        Sends an automated alert email using Gmail API.
        """
        target_email = self.alert_email.strip()
        if not self.enable_email or not target_email:
            logger.debug("Email notifications disabled or no alert recipient configured.")
            return False

        if message_id and self.has_notified(message_id, "EMAIL"):
            logger.debug("Email notification already sent for email '%s'", message_id)
            return True

        dt_str = (received_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        email_subject = f"[{priority}] Vendor Action Alert: {subject[:60]}"
        email_body = (
            f"Smart Vendor Automation Alert\n"
            f"----------------------------------------\n"
            f"Priority      : {priority}\n"
            f"Sender        : {sender_email}\n"
            f"Subject       : {subject}\n"
            f"Received At   : {dt_str}\n"
            f"Message ID    : {message_id or 'N/A'}\n"
            f"----------------------------------------\n"
            f"The vendor attachments are currently being downloaded and processed into inventory.\n"
        )

        try:
            if gmail_client and hasattr(gmail_client, "users"):
                # Real Gmail API sending
                msg = MIMEText(email_body)
                msg["to"] = target_email
                msg["subject"] = email_subject
                raw_payload = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

                gmail_client.users().messages().send(
                    userId="me",
                    body={"raw": raw_payload},
                ).execute()
                logger.info("Sent alert email notification to '%s'", target_email)
            else:
                # Simulation / Offline fallback
                logger.info("[NOTIFICATION SIMULATION] Alert email drafted for '%s' to '%s'", sender_email, target_email)

            if message_id:
                self.log_notification(message_id, "EMAIL", target_email, email_subject, "SENT")
            return True
        except Exception as exc:
            logger.warning("Email notification delivery failed (safe fallback): %s", exc)
            if message_id:
                self.log_notification(message_id, "EMAIL", target_email, email_subject, "FAILED", str(exc))
            return False

    def notify_all(
        self,
        sender_name: str,
        sender_email: str,
        subject: str,
        priority: str = "Normal",
        received_at: Optional[datetime] = None,
        message_id: Optional[str] = None,
        gmail_client: Optional[Any] = None,
    ) -> Dict[str, bool]:
        """
        Executes both desktop and email notifications in a completely fault-isolated manner.
        """
        results = {
            "desktop": False,
            "email": False,
        }

        # 1. Desktop Notification
        try:
            results["desktop"] = self.send_desktop_notification(
                sender_name=sender_name,
                subject=subject,
                priority=priority,
                message_id=message_id,
            )
        except Exception as exc:
            logger.warning("Desktop notification error intercepted: %s", exc)

        # 2. Email Notification
        try:
            results["email"] = self.send_email_notification(
                sender_email=sender_email,
                subject=subject,
                priority=priority,
                received_at=received_at,
                message_id=message_id,
                gmail_client=gmail_client,
            )
        except Exception as exc:
            logger.warning("Email notification error intercepted: %s", exc)

        return results
