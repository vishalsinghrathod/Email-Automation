"""
Database Service Module
Manages SQLite connection, schema migration, vendor registry,
idempotent inventory upserts, email audit logs, and rejected records.
"""

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

from database.db import init_database, get_db

logger = logging.getLogger("inventory_automation")


class DatabaseService:
    """
    Thread-safe SQLite database manager for vendor inventory operations.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def get_connection(self):
        """
        Creates and yields a SQLite connection with foreign keys enabled
        and Row factory for dictionary-like access, cleanly closing on exit.
        """
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """
        Initializes database tables, constraints, and indexes from database/schema.sql.
        """
        init_database(self.db_path)

    # --------------------------------------------------------------------------
    # Vendor Management
    # --------------------------------------------------------------------------

    def register_vendor(self, name: str, email: str, vendor_code: Optional[str] = None) -> int:
        """
        Registers or updates a vendor. Returns vendor ID.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO vendors (name, email, vendor_code, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(name) DO UPDATE SET
                    email = excluded.email,
                    vendor_code = COALESCE(excluded.vendor_code, vendors.vendor_code),
                    is_active = 1;
            """, (name.strip(), email.strip().lower(), vendor_code))
            conn.commit()

            cursor.execute("SELECT id FROM vendors WHERE name = ? COLLATE NOCASE", (name.strip(),))
            row = cursor.fetchone()
            return row["id"]

    def seed_default_vendors(self) -> None:
        """
        Seeds standard default vendors for development and simulation.
        """
        default_vendors = [
            ("Apex Global Supplies", "vendor.apex@gmail.com", "VEND-APEX"),
            ("Nexus Logistics", "sales@nexuslogistics.com", "VEND-NEXUS"),
            ("Prime Hardware Corp", "orders@primehardware.com", "VEND-PRIME"),
            ("Global Tech Supplies", "global.supplier@example.com", "VEND-GTS"),
        ]
        for name, email, code in default_vendors:
            self.register_vendor(name, email, code)
        logger.info("Default vendors seeded successfully.")

    def get_vendor_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves vendor details by email address (case-insensitive).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM vendors WHERE email = ? COLLATE NOCASE AND is_active = 1",
                (email.strip().lower(),)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_vendor_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves vendor details by name (case-insensitive).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM vendors WHERE name = ? COLLATE NOCASE AND is_active = 1",
                (name.strip(),)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_vendor_emails(self) -> List[str]:
        """
        Returns list of lowercase email addresses for all active vendors.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT email FROM vendors WHERE is_active = 1")
            return [row["email"].lower() for row in cursor.fetchall()]

    def get_or_create_vendor(self, name: str, fallback_email: Optional[str] = None) -> int:
        """
        Gets existing vendor ID by name, or creates a new active vendor record.
        """
        vendor = self.get_vendor_by_name(name)
        if vendor:
            return vendor["id"]

        generated_email = fallback_email or f"{name.lower().replace(' ', '.')}@vendor.autogen"
        return self.register_vendor(name=name, email=generated_email)

    # --------------------------------------------------------------------------
    # Email Logs & Idempotency
    # --------------------------------------------------------------------------

    def is_email_processed(self, message_id: str) -> bool:
        """
        Checks if Gmail message_id has already been successfully processed.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM email_logs WHERE message_id = ? AND status IN ('SUCCESS', 'PARTIAL')",
                (message_id,)
            )
            return cursor.fetchone() is not None

    def log_email(
        self,
        message_id: str,
        sender_email: str,
        subject: str,
        received_at: Optional[datetime],
        attachment_count: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> int:
        """
        Records email processing result in email_logs. Returns record ID.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO email_logs (
                    message_id, sender_email, subject, received_at,
                    attachment_count, status, error_message, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(message_id) DO UPDATE SET
                    status = excluded.status,
                    error_message = excluded.error_message,
                    processed_at = CURRENT_TIMESTAMP;
            """, (
                message_id,
                sender_email,
                subject,
                received_at.isoformat() if received_at else None,
                attachment_count,
                status,
                error_message,
            ))
            conn.commit()
            return cursor.lastrowid

    # --------------------------------------------------------------------------
    # File Deduplication Checksum
    # --------------------------------------------------------------------------

    def is_file_already_processed(self, file_hash: str) -> bool:
        """
        Checks if file with given SHA256 hash has already been processed.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM processed_files WHERE file_hash = ?", (file_hash,))
            return cursor.fetchone() is not None

    def record_processed_file(self, file_hash: str, filename: str, file_size: int) -> None:
        """
        Records file checksum to prevent duplicate processing.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO processed_files (file_hash, filename, file_size)
                VALUES (?, ?, ?)
            """, (file_hash, filename, file_size))
            conn.commit()

    # --------------------------------------------------------------------------
    # Inventory Records Upsert & Batch Insert
    # --------------------------------------------------------------------------

    def upsert_inventory_records(self, records: List[Dict[str, Any]], source_file: str) -> Tuple[int, int]:
        """
        Upserts valid inventory records into SQLite.
        Returns tuple of (inserted_or_updated_count, error_count).
        Uses SQLite ON CONFLICT(vendor_id, product_id) DO UPDATE.
        """
        if not records:
            return 0, 0

        success_count = 0
        error_count = 0

        with self.get_connection() as conn:
            cursor = conn.cursor()
            for record in records:
                try:
                    # Resolve vendor_id
                    vendor_name = record["vendor_name"]
                    vendor_id = self.get_or_create_vendor(vendor_name)

                    invoice_val = str(record.get("invoice_no") or "").strip() or None

                    cursor.execute("""
                        INSERT INTO inventory_items (
                            vendor_id, product_id, product_name, sku,
                            quantity, price, updated_date, invoice_no, source_file, last_imported_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(vendor_id, product_id) DO UPDATE SET
                            product_name = excluded.product_name,
                            sku = excluded.sku,
                            quantity = excluded.quantity,
                            price = excluded.price,
                            updated_date = excluded.updated_date,
                            invoice_no = COALESCE(excluded.invoice_no, inventory_items.invoice_no),
                            source_file = excluded.source_file,
                            last_imported_at = CURRENT_TIMESTAMP;
                    """, (
                        vendor_id,
                        str(record["product_id"]).strip(),
                        str(record["product_name"]).strip(),
                        str(record["sku"]).strip(),
                        int(record["quantity"]),
                        float(record["price"]),
                        str(record["updated_date"]).strip(),
                        invoice_val,
                        source_file,
                    ))
                    success_count += 1
                except Exception as exc:
                    logger.error("Failed to upsert record %s: %s", record, exc)
                    error_count += 1

            conn.commit()

        return success_count, error_count

    # --------------------------------------------------------------------------
    # Rejected Records
    # --------------------------------------------------------------------------

    def record_rejected_records(
        self,
        rejected_rows: List[Dict[str, Any]],
        source_file: str,
        email_log_id: Optional[int] = None,
    ) -> int:
        """
        Stores rejected inventory records with reasons for auditing.
        """
        if not rejected_rows:
            return 0

        count = 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for item in rejected_rows:
                cursor.execute("""
                    INSERT INTO rejected_records (
                        email_log_id, source_file, vendor_name,
                        product_id, raw_data, rejection_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    email_log_id,
                    source_file,
                    str(item.get("vendor_name", "")),
                    str(item.get("product_id", "")),
                    str(item.get("raw_data", "")),
                    str(item.get("rejection_reason", "UNKNOWN_ERROR")),
                ))
                count += 1
            conn.commit()

        return count

    # --------------------------------------------------------------------------
    # Processing Run Summary
    # --------------------------------------------------------------------------

    def record_processing_run(
        self,
        duration_seconds: float,
        emails_inspected: int,
        emails_processed: int,
        files_downloaded: int,
        records_valid: int,
        records_rejected: int,
        status: str,
        summary_text: str,
    ) -> int:
        """
        Stores high-level automation run statistics.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO processing_runs (
                    duration_seconds, emails_inspected, emails_processed,
                    files_downloaded, records_valid, records_rejected,
                    status, summary_text, run_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                duration_seconds,
                emails_inspected,
                emails_processed,
                files_downloaded,
                records_valid,
                records_rejected,
                status,
                summary_text,
            ))
            conn.commit()
            return cursor.lastrowid

    # --------------------------------------------------------------------------
    # Dashboard & Query Methods
    # --------------------------------------------------------------------------

    def get_summary_statistics(self) -> Dict[str, Any]:
        """
        Returns high-level statistics of inventory, emails, and vendors.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS total_vendors FROM vendors WHERE is_active = 1")
            total_vendors = cursor.fetchone()["total_vendors"]

            cursor.execute("SELECT COUNT(*) AS total_items, SUM(quantity) AS total_units FROM inventory_items")
            item_row = cursor.fetchone()
            total_items = item_row["total_items"] or 0
            total_units = item_row["total_units"] or 0

            cursor.execute("SELECT COUNT(*) AS total_emails FROM email_logs WHERE status = 'SUCCESS'")
            total_emails = cursor.fetchone()["total_emails"]

            cursor.execute("SELECT COUNT(*) AS total_rejected FROM rejected_records")
            total_rejected = cursor.fetchone()["total_rejected"]

            return {
                "active_vendors": total_vendors,
                "inventory_distinct_products": total_items,
                "total_units_in_stock": total_units,
                "emails_processed_successfully": total_emails,
                "total_rejected_records": total_rejected,
            }

    def get_latest_inventory(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieves the latest inventory records joined with vendor names.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    v.name AS vendor_name,
                    i.product_id,
                    i.product_name,
                    i.sku,
                    i.quantity,
                    i.price,
                    i.updated_date,
                    i.source_file,
                    i.last_imported_at
                FROM inventory_items i
                JOIN vendors v ON i.vendor_id = v.id
                ORDER BY i.last_imported_at DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_all_inventory_with_alerts(
        self,
        low_stock_threshold: int = 10,
        price_spike_threshold: float = 500.0,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all inventory items with computed alert status badges.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    v.name AS vendor_name,
                    i.product_id,
                    i.product_name,
                    i.sku,
                    i.quantity,
                    i.price,
                    i.updated_date,
                    COALESCE(i.invoice_no, 'N/A') AS invoice_no,
                    i.source_file,
                    i.last_imported_at
                FROM inventory_items i
                JOIN vendors v ON i.vendor_id = v.id
                ORDER BY i.quantity ASC, i.last_imported_at DESC
            """)
            items = []
            for row in cursor.fetchall():
                d = dict(row)
                qty = d["quantity"]
                price = d["price"]
                alerts = []
                if qty == 0:
                    alerts.append("CRITICAL: Out of Stock (0 units)")
                elif qty <= low_stock_threshold:
                    alerts.append(f"CRITICAL: Low Stock ({qty} units left)")
                if price >= price_spike_threshold:
                    alerts.append(f"WARNING: High Value Item (${price:.2f})")
                d["alerts"] = alerts
                d["has_alert"] = len(alerts) > 0
                d["alert_severity"] = "CRITICAL" if any("CRITICAL" in a for a in alerts) else ("WARNING" if alerts else "NORMAL")
                items.append(d)
            return items

    def get_latest_rejected_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieves the latest rejected records for audit and dashboard display.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, source_file, vendor_name, product_id, raw_data, rejection_reason, created_at
                FROM rejected_records
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_processing_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Retrieves the latest execution run history summaries.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, run_timestamp, duration_seconds, emails_inspected, emails_processed,
                       files_downloaded, records_valid, records_rejected, status, summary_text
                FROM processing_runs
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_all_vendors(self) -> List[Dict[str, Any]]:
        """
        Retrieves all registered vendors.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, email, vendor_code, is_active, created_at FROM vendors ORDER BY name ASC")
            return [dict(row) for row in cursor.fetchall()]

    # --------------------------------------------------------------------------
    # Monitored Accounts & Linked Inboxes (Multi-Email Monitoring)
    # --------------------------------------------------------------------------

    def get_monitored_accounts(self) -> List[Dict[str, Any]]:
        """
        Retrieves all configured monitored email accounts.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, account_name, email_address, provider_type, is_active,
                       desktop_alerts, total_emails_detected, important_emails_detected,
                       last_checked_at, created_at
                FROM monitored_accounts
                ORDER BY is_active DESC, created_at DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def add_monitored_account(
        self,
        account_name: str,
        email_address: str,
        provider_type: str = "GMAIL",
        desktop_alerts: bool = True,
    ) -> int:
        """
        Adds or updates a monitored email account. Returns record ID.
        """
        email_clean = email_address.strip().lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO monitored_accounts (
                    account_name, email_address, provider_type, is_active, desktop_alerts
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(email_address) DO UPDATE SET
                    account_name = excluded.account_name,
                    provider_type = excluded.provider_type,
                    is_active = 1,
                    desktop_alerts = excluded.desktop_alerts;
            """, (account_name.strip(), email_clean, provider_type, 1 if desktop_alerts else 0))
            conn.commit()
            return cursor.lastrowid

    def toggle_monitored_account(self, account_id: int, is_active: bool) -> bool:
        """
        Activates or pauses monitoring for a linked account.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE monitored_accounts SET is_active = ? WHERE id = ?",
                (1 if is_active else 0, account_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_monitored_account(self, account_id: int) -> bool:
        """
        Removes a monitored email account.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM monitored_accounts WHERE id = ?", (account_id,))
            conn.commit()
            return cursor.rowcount > 0

    def record_monitored_stats(self, email_address: str, is_important: bool = False) -> None:
        """
        Increments email detection counters and updates last_checked_at timestamp.
        """
        email_clean = email_address.strip().lower()
        important_inc = 1 if is_important else 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE monitored_accounts
                SET total_emails_detected = total_emails_detected + 1,
                    important_emails_detected = important_emails_detected + ?,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE email_address = ? COLLATE NOCASE;
            """, (important_inc, email_clean))
            conn.commit()

    def get_all_emails_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieves all recorded incoming emails with priority and status highlights.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, message_id, sender_name, sender_email, subject,
                       body_preview, received_at, priority, status, attachment_count, processed_at
                FROM emails
                ORDER BY received_at DESC, id DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def seed_default_monitored_accounts(self) -> None:
        """
        Seeds standard vendor feeds and inboxes if none exist.
        """
        accounts = self.get_monitored_accounts()
        if not accounts:
            defaults = [
                ("Apex Global Supplies Feed", "vendor.apex@gmail.com", "GMAIL"),
                ("Prime Hardware Ingestion", "orders@primehardware.com", "GMAIL"),
                ("Nexus Logistics Updates", "sales@nexuslogistics.com", "GMAIL"),
                ("Purchasing Desk Inbound", "purchasing@company.com", "IMAP"),
            ]
            for name, email, prov in defaults:
                self.add_monitored_account(account_name=name, email_address=email, provider_type=prov)
            logger.info("Seeded default monitored email accounts.")

    def reset_demo_data(self) -> None:
        """
        Clears demo email logs and checksums to allow repeatable demo execution.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM emails WHERE message_id LIKE 'demo_%';")
            cursor.execute("DELETE FROM email_logs WHERE message_id LIKE 'demo_%';")
            cursor.execute("DELETE FROM processing_queue WHERE message_id LIKE 'demo_%';")
            cursor.execute("DELETE FROM notifications WHERE message_id LIKE 'demo_%';")
            cursor.execute("DELETE FROM attachments WHERE message_id LIKE 'demo_%';")
            cursor.execute("DELETE FROM processed_files WHERE filename LIKE '%_Nexus_%' OR filename LIKE '%_Apex_%' OR filename LIKE '%_Prime_%';")
            cursor.execute("DELETE FROM rejected_records WHERE source_file LIKE '%_Nexus_%' OR source_file LIKE '%_Apex_%' OR source_file LIKE '%_Prime_%';")
            cursor.execute("DELETE FROM inventory_items WHERE source_file LIKE '%_Nexus_%' OR source_file LIKE '%_Apex_%' OR source_file LIKE '%_Prime_%';")
            conn.commit()
            logger.info("Demo data reset successfully.")
