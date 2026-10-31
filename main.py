"""
Main Application Orchestrator
Vendor Inventory Email Automation System
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import (
    DATABASE_PATH,
    DOWNLOADS_DIR,
    GMAIL_BATCH_SIZE,
    GMAIL_CREDENTIALS_PATH,
    PROCESSED_DIR,
    LOW_STOCK_ALERT_THRESHOLD,
    PRICE_SPIKE_ALERT_THRESHOLD,
    setup_logging,
)
from services.alert_service import AlertService
from services.attachment_service import AttachmentService
from services.database_service import DatabaseService
from services.email_classifier import EmailClassifier
from services.email_monitor import EmailMonitor
from services.gmail_service import DemoInboxProvider, GmailService
from services.inventory_processor import InventoryProcessor
from services.notification_service import NotificationService
from services.queue_service import QueueService
from services.report_service import ReportService, RunMetrics

logger = setup_logging()


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Vendor Inventory & Invoice Email Automation System",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--demo",
        "--offline",
        action="store_true",
        help="Run pipeline in offline demo mode using simulated vendor emails and sample files (CSV, XLSX, PDF)",
    )
    parser.add_argument(
        "--reset-demo",
        action="store_true",
        help="Reset demo records from database so the demo can be re-run cleanly during presentations",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the interactive HTML Alert Dashboard in your default web browser",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        default=True,
        help="Execute a single processing run and exit",
    )
    parser.add_argument(
        "--seed-vendors",
        action="store_true",
        help="Seed default registered vendors into the database",
    )
    parser.add_argument(
        "--status",
        "--summary",
        action="store_true",
        help="Display current database summary statistics and exit",
    )
    parser.add_argument(
        "--view-inventory",
        action="store_true",
        help="Print top latest inventory items with alert highlights from the database",
    )
    parser.add_argument(
        "--view-alerts",
        action="store_true",
        help="Display active critical low-stock and price anomaly alerts",
    )
    parser.add_argument(
        "--ui",
        "--web",
        action="store_true",
        help="Launch the complete Employee Web Portal in browser with interactive controls",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8080)),
        help="Port to run the Employee Web Portal on (default: 8080 or $PORT)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically launch web browser when starting Web Portal",
    )
    return parser.parse_args()


def display_status(db_service: DatabaseService) -> None:
    """Prints current database statistics to the console."""
    stats = db_service.get_summary_statistics()
    items = db_service.get_all_inventory_with_alerts(
        low_stock_threshold=LOW_STOCK_ALERT_THRESHOLD,
        price_spike_threshold=PRICE_SPIKE_ALERT_THRESHOLD,
    )
    critical_count = sum(1 for it in items if it.get("alert_severity") == "CRITICAL")
    warning_count = sum(1 for it in items if it.get("alert_severity") == "WARNING")

    print("\n" + "=" * 60)
    print("        INVENTORY SYSTEM - EXECUTIVE STATUS")
    print("=" * 60)
    print(f"  Active Registered Vendors       : {stats['active_vendors']}")
    print(f"  Distinct Products in Stock      : {stats['inventory_distinct_products']}")
    print(f"  Total Inventory Units in Stock  : {stats['total_units_in_stock']}")
    print(f"  Emails Processed Successfully   : {stats['emails_processed_successfully']}")
    print(f"  Total Rejected Records Logged   : {stats['total_rejected_records']}")
    print("-" * 60)
    print(f"  CRITICAL Stock Alerts (Shortage): {critical_count}")
    print(f"  WARNING Price Alerts            : {warning_count}")
    print("=" * 60 + "\n")


def display_inventory(db_service: DatabaseService, limit: int = 30) -> None:
    """Prints the latest inventory records with clear alert highlights."""
    items = db_service.get_all_inventory_with_alerts(
        low_stock_threshold=LOW_STOCK_ALERT_THRESHOLD,
        price_spike_threshold=PRICE_SPIKE_ALERT_THRESHOLD,
    )[:limit]

    if not items:
        print("\nNo inventory items found in database.\n")
        return

    try:
        from tabulate import tabulate
        from colorama import Fore, Style

        headers = ["Vendor", "SKU", "Product Name", "Qty", "Price ($)", "Invoice #", "Alert Status"]
        table_rows = []
        for row in items:
            qty = row["quantity"]
            price = row["price"]
            invoice = row.get("invoice_no") or "-"

            # Highlight logic
            if qty == 0:
                alert_text = f"{Fore.RED}[CRITICAL: OUT OF STOCK]{Style.RESET_ALL}"
                qty_text = f"{Fore.RED}{qty}{Style.RESET_ALL}"
            elif qty <= LOW_STOCK_ALERT_THRESHOLD:
                alert_text = f"{Fore.RED}[CRITICAL: LOW STOCK]{Style.RESET_ALL}"
                qty_text = f"{Fore.RED}{qty}{Style.RESET_ALL}"
            elif price >= PRICE_SPIKE_ALERT_THRESHOLD:
                alert_text = f"{Fore.YELLOW}[WARNING: HIGH PRICE]{Style.RESET_ALL}"
                qty_text = str(qty)
            else:
                alert_text = f"{Fore.GREEN}[OK: HEALTHY]{Style.RESET_ALL}"
                qty_text = str(qty)

            table_rows.append([
                row["vendor_name"][:20],
                row["sku"],
                row["product_name"][:26],
                qty_text,
                f"${price:.2f}",
                invoice,
                alert_text,
            ])
        print("\n" + tabulate(table_rows, headers=headers, tablefmt="grid") + "\n")
    except ImportError:
        print(f"\n{'Vendor':<20} {'SKU':<12} {'Product':<25} {'Qty':<6} {'Price':<8} {'Invoice':<12} {'Alert'}")
        print("-" * 90)
        for row in items:
            qty = row["quantity"]
            status = "CRITICAL" if qty <= LOW_STOCK_ALERT_THRESHOLD else "OK"
            print(
                f"{row['vendor_name'][:18]:<20} "
                f"{row['sku']:<12} "
                f"{row['product_name'][:23]:<25} "
                f"{qty:<6} "
                f"${row['price']:<7.2f} "
                f"{str(row.get('invoice_no') or '-'):<12} "
                f"{status}"
            )
        print("-" * 90 + "\n")


def display_alerts(db_service: DatabaseService) -> None:
    """Displays only active critical and warning alerts."""
    items = db_service.get_all_inventory_with_alerts(
        low_stock_threshold=LOW_STOCK_ALERT_THRESHOLD,
        price_spike_threshold=PRICE_SPIKE_ALERT_THRESHOLD,
    )
    alert_items = [it for it in items if it.get("has_alert")]

    if not alert_items:
        print("\n[OK] No active alerts. All stock and price levels are healthy!\n")
        return

    print("\n" + "=" * 70)
    print("                 ACTIVE SYSTEM ALERTS & HIGHLIGHTS")
    print("=" * 70)
    for it in alert_items:
        prefix = "[CRITICAL]" if it["alert_severity"] == "CRITICAL" else "[WARNING]"
        alerts_str = "; ".join(it["alerts"])
        print(f" {prefix:<10} | Vendor: {it['vendor_name']} | SKU: {it['sku']}")
        print(f"             Item: {it['product_name']} | Qty: {it['quantity']} | Price: ${it['price']:.2f}")
        print(f"             Alert Detail: {alerts_str}")
        print("-" * 70)
    print("=" * 70 + "\n")


def run_pipeline(demo_mode: bool = False, open_dashboard: bool = False) -> RunMetrics:
    """
    Executes the complete email ingestion and inventory processing workflow.
    """
    metrics = RunMetrics()
    logger.info("Starting Vendor Inventory Automation run (Demo Mode: %s)...", demo_mode)

    # Initialize Services
    db_service = DatabaseService(DATABASE_PATH)
    attachment_service = AttachmentService(DOWNLOADS_DIR)
    processor = InventoryProcessor()
    report_service = ReportService(PROCESSED_DIR)
    classifier = EmailClassifier()
    notification_service = NotificationService(db_path=DATABASE_PATH)
    queue_service = QueueService(db_path=DATABASE_PATH)

    # Crash recovery: restore interrupted processing jobs
    recovered_jobs = queue_service.recover_unfinished_jobs()
    if recovered_jobs > 0:
        logger.info("Crash Recovery: %d interrupted jobs restored to PENDING queue.", recovered_jobs)

    cycle_valid_records = []
    cycle_rejected_records = []

    # Determine Email Provider
    email_service = None
    if demo_mode:
        email_service = DemoInboxProvider()
    else:
        # Check if credentials exist for production Gmail API
        if not GMAIL_CREDENTIALS_PATH.exists():
            err_msg = (
                f"Gmail credentials file not found at '{GMAIL_CREDENTIALS_PATH}'. "
                "Please configure credentials.json or run with Demo Mode."
            )
            logger.warning(err_msg)
            metrics.errors.append(err_msg)
            metrics.overall_status = "WARNING"
            return metrics
        else:
            gmail = GmailService()
            if gmail.authenticate():
                email_service = gmail
            else:
                err_msg = "Gmail authentication failed. Please check credentials or re-authenticate."
                logger.error(err_msg)
                metrics.errors.append(err_msg)
                metrics.overall_status = "FAILED"
                return metrics

    # Step 1: Monitor, Classify & Enqueue Incoming Emails (Modules A, B, C, H)
    email_monitor = EmailMonitor(
        gmail_service=email_service,
        db_path=DATABASE_PATH,
        classifier=classifier,
        notification_service=notification_service,
        queue_service=queue_service,
    )

    try:
        emails = email_monitor.run_reconciliation_cycle(max_results=GMAIL_BATCH_SIZE)
        metrics.emails_inspected = len(emails)
    except Exception as exc:
        err_msg = f"Failed to inspect emails: {exc}"
        logger.error(err_msg)
        metrics.errors.append(err_msg)
        metrics.end_time = datetime.now()
        report_service.save_run_summary(metrics)
        return metrics

    # Step 2: Fetch Prioritized Processing Batch from Queue (High Priority First)
    queued_batch = queue_service.fetch_next_batch(batch_size=GMAIL_BATCH_SIZE)

    if not queued_batch and not emails:
        logger.info("No unread or pending inventory emails found. Execution cycle complete.")
        metrics.end_time = datetime.now()
        report_service.save_run_summary(metrics)
        return metrics

    emails_map = {e.message_id: e for e in emails}

    # Step 3: Process Queued Emails in Priority Order
    for job in queued_batch:
        msg_id = job["message_id"]
        email_msg = emails_map.get(msg_id)

        if not email_msg:
            # If job was recovered from previous run but not in current fetch pass
            logger.debug("Message '%s' queued from previous run, searching in inbox...", msg_id)
            continue

        sender_email = email_msg.sender_email
        subject = email_msg.subject

        logger.info("Processing [Priority Level %d] email '%s' from '%s'", job["priority_level"], msg_id, sender_email)

        # Idempotency check: avoid processing same email twice
        if db_service.is_email_processed(msg_id):
            logger.info("Email '%s' already processed in previous run. Skipping.", msg_id)
            queue_service.mark_completed(msg_id)
            metrics.emails_skipped += 1
            continue

        # Extract attachments
        attachment_parts = [p for p in email_msg.raw_parts if p.get("filename")]

        if not attachment_parts:
            logger.warning("Email '%s' has no attachments. Skipping.", msg_id)
            db_service.log_email(
                message_id=msg_id,
                sender_email=sender_email,
                subject=subject,
                received_at=email_msg.received_at,
                attachment_count=0,
                status="SKIPPED",
                error_message="No attachments present",
            )
            queue_service.mark_completed(msg_id)
            metrics.emails_skipped += 1
            continue

        email_status = "SUCCESS"
        email_error: Optional[str] = None
        processed_attachments_for_email = 0

        for part in attachment_parts:
            raw_filename = part.get("filename", "")

            # Extract and download attachment
            try:
                client = getattr(email_service, "service", None)
                att_info = attachment_service.extract_and_save_from_gmail_part(
                    part=part,
                    gmail_client_or_service=client,
                    message_id=msg_id,
                    vendor_prefix=email_msg.sender_name,
                )
            except Exception as exc:
                err_text = f"Failed to download attachment '{raw_filename}': {exc}"
                logger.error(err_text)
                metrics.errors.append(err_text)
                email_status = "PARTIAL"
                continue

            if not att_info:
                continue

            # Validate format and size
            is_valid_att, rej_reason = attachment_service.validate_attachment(
                att_info.original_filename,
                att_info.size_bytes,
            )

            if not is_valid_att:
                logger.warning(
                    "Attachment '%s' rejected during format check: %s",
                    att_info.original_filename,
                    rej_reason,
                )
                rejected_entry = {
                    "vendor_name": email_msg.sender_name,
                    "product_id": "",
                    "raw_data": f"Filename: {att_info.original_filename}, Size: {att_info.size_bytes}",
                    "rejection_reason": rej_reason,
                }
                db_service.record_rejected_records(
                    rejected_rows=[rejected_entry],
                    source_file=att_info.saved_path.name,
                )
                cycle_rejected_records.append(rejected_entry)
                metrics.total_rejected_records += 1
                metrics.rejection_reasons_breakdown[rej_reason] = (
                    metrics.rejection_reasons_breakdown.get(rej_reason, 0) + 1
                )
                continue

            # Deduplication: Check if exact file hash was already imported
            if db_service.is_file_already_processed(att_info.file_hash):
                logger.info(
                    "File '%s' (hash: %s...) has already been imported. Skipping.",
                    att_info.saved_path.name,
                    att_info.file_hash[:8],
                )
                continue

            metrics.files_downloaded += 1
            metrics.processed_files.append(att_info.saved_path.name)

            # Process file contents (CSV, XLSX, PDF, TXT)
            proc_result = processor.process_file(
                file_path=att_info.saved_path,
                fallback_vendor_name=email_msg.sender_name,
            )

            if not proc_result.success:
                err_text = f"File '{att_info.saved_path.name}' processing failed: {proc_result.error_message}"
                logger.error(err_text)
                metrics.errors.append(err_text)
                rejected_entry = {
                    "vendor_name": email_msg.sender_name,
                    "product_id": "",
                    "raw_data": f"File parse failure: {att_info.saved_path.name}",
                    "rejection_reason": proc_result.error_message or "PARSE_ERROR",
                }
                db_service.record_rejected_records(
                    rejected_rows=[rejected_entry],
                    source_file=att_info.saved_path.name,
                )
                cycle_rejected_records.append(rejected_entry)
                metrics.total_rejected_records += 1
                email_status = "PARTIAL"
                continue

            # Upsert valid records into database
            if proc_result.valid_records:
                success_count, err_count = db_service.upsert_inventory_records(
                    records=proc_result.valid_records,
                    source_file=att_info.saved_path.name,
                )
                metrics.total_valid_records += success_count
                cycle_valid_records.extend(proc_result.valid_records)
                if err_count > 0:
                    email_status = "PARTIAL"

                # Export cleaned data
                report_service.export_valid_records_csv(proc_result.valid_records)

            # Record rejected records into audit table & export
            if proc_result.rejected_records:
                db_service.record_rejected_records(
                    rejected_rows=proc_result.rejected_records,
                    source_file=att_info.saved_path.name,
                )
                cycle_rejected_records.extend(proc_result.rejected_records)
                metrics.total_rejected_records += len(proc_result.rejected_records)
                report_service.export_rejected_records_csv(proc_result.rejected_records)

                for row in proc_result.rejected_records:
                    reason = row.get("rejection_reason", "UNKNOWN")
                    metrics.rejection_reasons_breakdown[reason] = (
                        metrics.rejection_reasons_breakdown.get(reason, 0) + 1
                    )

            # Record file checksum
            db_service.record_processed_file(
                file_hash=att_info.file_hash,
                filename=att_info.saved_path.name,
                file_size=att_info.size_bytes,
            )
            processed_attachments_for_email += 1

        # Mark email processed in Gmail, audit table, and persistent queue
        email_service.mark_email_as_processed(msg_id)
        db_service.log_email(
            message_id=msg_id,
            sender_email=sender_email,
            subject=subject,
            received_at=email_msg.received_at,
            attachment_count=processed_attachments_for_email,
            status=email_status,
            error_message=email_error,
        )

        if email_status in ("SUCCESS", "PARTIAL"):
            queue_service.mark_completed(msg_id)
            metrics.emails_processed += 1
        else:
            queue_service.mark_failed(msg_id, email_error or "Processing failure")
            metrics.emails_failed += 1

    # Finalize Metrics and Reporting
    metrics.end_time = datetime.now()
    summary_text = report_service.generate_summary_text(metrics)
    report_service.save_run_summary(metrics)

    # Record run in database
    db_service.record_processing_run(
        duration_seconds=metrics.duration_seconds,
        emails_inspected=metrics.emails_inspected,
        emails_processed=metrics.emails_processed,
        files_downloaded=metrics.files_downloaded,
        records_valid=metrics.total_valid_records,
        records_rejected=metrics.total_rejected_records,
        status=metrics.overall_status,
        summary_text=summary_text,
    )

    # Print summary to console
    print("\n" + summary_text + "\n")

    # Live Alerts & Anomaly Highlights Analysis
    alert_service = AlertService()
    alert_report = alert_service.analyze_batch(
        valid_records=cycle_valid_records,
        rejected_records=cycle_rejected_records,
        source_file="batch_import",
    )
    # 1. Print visual colored highlights in console
    alert_service.render_console_alerts(alert_report)

    # 2. Update interactive HTML Alert Dashboard
    stats = db_service.get_summary_statistics()
    all_inventory = db_service.get_all_inventory_with_alerts(
        low_stock_threshold=LOW_STOCK_ALERT_THRESHOLD,
        price_spike_threshold=PRICE_SPIKE_ALERT_THRESHOLD,
    )
    dashboard_file = alert_service.generate_html_dashboard(
        report=alert_report,
        db_summary=stats,
        all_inventory_items=all_inventory,
    )
    print(f"Interactive Alert Dashboard updated: {dashboard_file.resolve()}")

    if open_dashboard:
        alert_service.open_dashboard_in_browser()

    return metrics


def main() -> None:
    """Entry point for command-line execution."""
    args = parse_arguments()
    db_service = DatabaseService(DATABASE_PATH)
    alert_service = AlertService()

    if args.reset_demo:
        db_service.reset_demo_data()
        print("\n[OK] Demo data successfully reset. You can now re-run --demo cleanly.\n")
        return

    if args.seed_vendors:
        db_service.seed_default_vendors()
        print("Default registered vendors seeded successfully.")
        return

    if args.status:
        display_status(db_service)
        return

    if args.view_inventory:
        display_inventory(db_service)
        return

    if args.view_alerts:
        display_alerts(db_service)
        return

    if args.dashboard and not args.demo:
        alert_service.open_dashboard_in_browser()
        return

    if args.ui:
        from services.web_service import start_web_server
        start_web_server(port=args.port, open_browser=not args.no_browser)
        return

    # Seed vendors if first run
    stats = db_service.get_summary_statistics()
    if stats["active_vendors"] == 0:
        db_service.seed_default_vendors()

    # Run the processing pipeline
    run_pipeline(demo_mode=args.demo, open_dashboard=args.dashboard)


if __name__ == "__main__":
    main()
