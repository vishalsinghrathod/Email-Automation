"""
Report Service Module
Generates human-readable processing summaries, rejected record CSV exports,
and execution metrics for monitoring and auditing.
"""

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import PROCESSED_DIR

logger = logging.getLogger("inventory_automation")


@dataclass
class RunMetrics:
    """Holds operational metrics for a single automation run."""
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    emails_inspected: int = 0
    emails_processed: int = 0
    emails_skipped: int = 0
    emails_failed: int = 0
    files_downloaded: int = 0
    total_valid_records: int = 0
    total_rejected_records: int = 0
    rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    processed_files: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        end = self.end_time or datetime.now()
        return round((end - self.start_time).total_seconds(), 2)

    @property
    def overall_status(self) -> str:
        if self.errors or self.emails_failed > 0:
            return "WARNING" if self.total_valid_records > 0 else "FAILED"
        return "SUCCESS"


class ReportService:
    """
    Produces formatted summaries and export files for processed inventory batches.
    """

    def __init__(self, processed_dir: Path = PROCESSED_DIR):
        self.processed_dir = Path(processed_dir)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def export_rejected_records_csv(
        self,
        rejected_records: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Exports rejected records to a timestamped CSV file for auditing and vendor feedback.
        """
        if not rejected_records:
            return None

        timestamp = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = self.processed_dir / f"rejected_records_{timestamp}.csv"

        fieldnames = ["row_index", "vendor_name", "product_id", "rejection_reason", "raw_data"]

        with open(export_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rejected_records:
                writer.writerow(row)

        logger.info("Exported %d rejected records to '%s'", len(rejected_records), export_path.name)
        return export_path

    def export_valid_records_csv(
        self,
        valid_records: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Exports valid cleaned records to a timestamped CSV archive.
        """
        if not valid_records:
            return None

        timestamp = batch_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = self.processed_dir / f"cleaned_inventory_{timestamp}.csv"

        fieldnames = [
            "vendor_name",
            "product_id",
            "product_name",
            "sku",
            "quantity",
            "price",
            "updated_date",
        ]

        with open(export_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in valid_records:
                writer.writerow(row)

        logger.info("Exported %d cleaned records to '%s'", len(valid_records), export_path.name)
        return export_path

    def generate_summary_text(self, metrics: RunMetrics) -> str:
        """
        Builds a comprehensive ASCII summary report for logging and monitoring.
        """
        duration = metrics.duration_seconds
        lines = [
            "=" * 70,
            "       VENDOR INVENTORY AUTOMATION - RUN SUMMARY REPORT",
            "=" * 70,
            f"Execution Status    : {metrics.overall_status}",
            f"Started At          : {metrics.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Completed At        : {(metrics.end_time or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total Duration      : {duration} seconds",
            "-" * 70,
            "EMAIL INGESTION METRICS:",
            f"  • Emails Inspected  : {metrics.emails_inspected}",
            f"  • Emails Processed  : {metrics.emails_processed}",
            f"  • Emails Skipped    : {metrics.emails_skipped}",
            f"  • Emails Failed     : {metrics.emails_failed}",
            f"  • Files Downloaded  : {metrics.files_downloaded}",
            "-" * 70,
            "INVENTORY RECORD METRICS:",
            f"  • Valid Records     : {metrics.total_valid_records} (Upserted to Database)",
            f"  • Rejected Records  : {metrics.total_rejected_records} (Logged to Audit Table)",
        ]

        if metrics.rejection_reasons_breakdown:
            lines.append("  • Rejection Reasons Breakdown:")
            for reason, count in sorted(metrics.rejection_reasons_breakdown.items(), key=lambda x: -x[1]):
                lines.append(f"      - {reason}: {count}")

        if metrics.processed_files:
            lines.append("-" * 70)
            lines.append("ATTACHMENTS PROCESSED:")
            for fname in metrics.processed_files:
                lines.append(f"  • {fname}")

        if metrics.errors:
            lines.append("-" * 70)
            lines.append("EXECUTION ERRORS & ALERTS:")
            for err in metrics.errors:
                lines.append(f"  ! {err}")

        lines.append("=" * 70)
        return "\n".join(lines)

    def save_run_summary(self, metrics: RunMetrics) -> Path:
        """
        Saves text summary report to the processed directory.
        """
        timestamp = metrics.start_time.strftime("%Y%m%d_%H%M%S")
        report_path = self.processed_dir / f"run_summary_{timestamp}.txt"
        summary_text = self.generate_summary_text(metrics)

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(summary_text)

        logger.info("Saved run summary report to '%s'", report_path.name)
        return report_path
