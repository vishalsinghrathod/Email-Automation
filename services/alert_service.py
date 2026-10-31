"""
Alert & Highlighting Service Module
Detects stock shortages, price anomalies, and invoice events,
and generates color-coded terminal alerts and an interactive HTML dashboard.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import webbrowser

try:
    from colorama import Fore, Back, Style, init as colorama_init
    colorama_init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

from config.settings import (
    LOW_STOCK_ALERT_THRESHOLD,
    PRICE_SPIKE_ALERT_THRESHOLD,
    REPORTS_DIR,
    DASHBOARD_HTML_PATH,
)

logger = logging.getLogger("inventory_automation")


@dataclass
class AlertItem:
    """Represents a single highlighted alert item."""
    severity: str  # 'CRITICAL', 'WARNING', 'INFO'
    category: str  # 'OUT_OF_STOCK', 'LOW_STOCK', 'PRICE_SPIKE', 'REJECTED_RECORD', 'INVOICE_PROCESSED'
    vendor_name: str
    product_name: str
    sku: str
    quantity: int
    price: float
    invoice_no: Optional[str]
    message: str
    source_file: str


@dataclass
class AlertBatchReport:
    """Summary of all alerts generated during a processing run."""
    run_timestamp: datetime = field(default_factory=datetime.now)
    critical_alerts: List[AlertItem] = field(default_factory=list)
    warning_alerts: List[AlertItem] = field(default_factory=list)
    info_alerts: List[AlertItem] = field(default_factory=list)
    rejected_alerts: List[Dict[str, Any]] = field(default_factory=list)
    processed_items: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total_critical(self) -> int:
        return len(self.critical_alerts)

    @property
    def total_warnings(self) -> int:
        return len(self.warning_alerts)

    @property
    def total_rejections(self) -> int:
        return len(self.rejected_alerts)


class AlertService:
    """
    Analyzes processed records for business anomalies and produces
    color-coded terminal highlights and an executive HTML Alert Dashboard.
    """

    def __init__(
        self,
        low_stock_threshold: int = LOW_STOCK_ALERT_THRESHOLD,
        price_spike_threshold: float = PRICE_SPIKE_ALERT_THRESHOLD,
        dashboard_path: Path = DASHBOARD_HTML_PATH,
    ):
        self.low_stock_threshold = low_stock_threshold
        self.price_spike_threshold = price_spike_threshold
        self.dashboard_path = Path(dashboard_path)
        self.dashboard_path.parent.mkdir(parents=True, exist_ok=True)

    def analyze_batch(
        self,
        valid_records: List[Dict[str, Any]],
        rejected_records: List[Dict[str, Any]],
        source_file: str = "batch_import",
    ) -> AlertBatchReport:
        """
        Analyzes records for low stock, high prices, and data errors.
        """
        report = AlertBatchReport()
        report.processed_items = valid_records
        report.rejected_alerts = rejected_records

        for rec in valid_records:
            qty = int(rec.get("quantity", 0))
            price = float(rec.get("price", 0.0))
            vendor = rec.get("vendor_name", "Unknown Vendor")
            prod = rec.get("product_name", "Unknown Product")
            sku = rec.get("sku", "N/A")
            invoice = rec.get("invoice_no")

            # 1. Out of stock alert
            if qty == 0:
                report.critical_alerts.append(AlertItem(
                    severity="CRITICAL",
                    category="OUT_OF_STOCK",
                    vendor_name=vendor,
                    product_name=prod,
                    sku=sku,
                    quantity=qty,
                    price=price,
                    invoice_no=invoice,
                    message=f"ZERO UNITS IN STOCK! Immediate restock required.",
                    source_file=source_file,
                ))

            # 2. Low stock alert
            elif qty <= self.low_stock_threshold:
                report.critical_alerts.append(AlertItem(
                    severity="CRITICAL",
                    category="LOW_STOCK",
                    vendor_name=vendor,
                    product_name=prod,
                    sku=sku,
                    quantity=qty,
                    price=price,
                    invoice_no=invoice,
                    message=f"Low Stock Alert: Only {qty} units remaining (Threshold <= {self.low_stock_threshold})",
                    source_file=source_file,
                ))

            # 3. High Price / Price Anomaly alert
            if price >= self.price_spike_threshold:
                report.warning_alerts.append(AlertItem(
                    severity="WARNING",
                    category="PRICE_SPIKE",
                    vendor_name=vendor,
                    product_name=prod,
                    sku=sku,
                    quantity=qty,
                    price=price,
                    invoice_no=invoice,
                    message=f"High Value Item: Unit price ${price:.2f} exceeds warning limit (${self.price_spike_threshold:.2f})",
                    source_file=source_file,
                ))

            # 4. Invoice tracked info
            if invoice:
                report.info_alerts.append(AlertItem(
                    severity="INFO",
                    category="INVOICE_PROCESSED",
                    vendor_name=vendor,
                    product_name=prod,
                    sku=sku,
                    quantity=qty,
                    price=price,
                    invoice_no=invoice,
                    message=f"Invoice #{invoice} line item recorded.",
                    source_file=source_file,
                ))

        return report

    def render_console_alerts(self, report: AlertBatchReport) -> None:
        """
        Prints color-highlighted alert banners and tables in the terminal.
        """
        has_color = COLORAMA_AVAILABLE

        def c_red(text: str) -> str:
            return f"{Fore.RED}{Style.BRIGHT}{text}{Style.RESET_ALL}" if has_color else text

        def c_yellow(text: str) -> str:
            return f"{Fore.YELLOW}{Style.BRIGHT}{text}{Style.RESET_ALL}" if has_color else text

        def c_green(text: str) -> str:
            return f"{Fore.GREEN}{Style.BRIGHT}{text}{Style.RESET_ALL}" if has_color else text

        def c_cyan(text: str) -> str:
            return f"{Fore.CYAN}{Style.BRIGHT}{text}{Style.RESET_ALL}" if has_color else text

        total_alerts = report.total_critical + report.total_warnings + report.total_rejections

        print("\n" + "=" * 76)
        print(c_cyan("               LIVE INVENTORY ALERTS & ANOMALY HIGHLIGHTS"))
        print("=" * 76)

        if total_alerts == 0:
            print(c_green("  [OK] No critical alerts or anomalies detected. All stock levels healthy."))
            print("=" * 76 + "\n")
            return

        print(f"  Summary: {c_red(f'{report.total_critical} Critical Shortages')} | "
              f"{c_yellow(f'{report.total_warnings} Price Warnings')} | "
              f"{c_red(f'{report.total_rejections} Rejections Logged')}")
        print("-" * 76)

        # 1. Critical Alerts (Low / Zero Stock)
        if report.critical_alerts:
            print("\n" + c_red("[CRITICAL STOCK ALERTS]") + " (Action Required):")
            for alert in report.critical_alerts:
                invoice_str = f" [Inv: {alert.invoice_no}]" if alert.invoice_no else ""
                print(f"  {c_red('>>')} {c_red(alert.category)} | "
                      f"Vendor: {alert.vendor_name} | "
                      f"SKU: {alert.sku} | "
                      f"Item: {alert.product_name[:25]} | "
                      f"Stock: {c_red(str(alert.quantity))} units | "
                      f"Price: ${alert.price:.2f}{invoice_str}")
                print(f"     Reason: {alert.message}")

        # 2. Warning Alerts (Price Anomalies)
        if report.warning_alerts:
            print("\n" + c_yellow("[WARNING PRICE ALERTS]") + " (Review Pricing):")
            for alert in report.warning_alerts:
                print(f"  {c_yellow('>>')} {c_yellow(alert.category)} | "
                      f"Vendor: {alert.vendor_name} | "
                      f"SKU: {alert.sku} | "
                      f"Item: {alert.product_name[:25]} | "
                      f"Price: {c_yellow('$' + f'{alert.price:.2f}')} | "
                      f"Stock: {alert.quantity}")
                print(f"     Reason: {alert.message}")

        # 3. Rejected Records
        if report.rejected_alerts:
            print("\n" + c_red("[DATA INTEGRITY & REJECTION AUDIT]") + " (Vendor Error):")
            for rej in report.rejected_alerts[:5]:
                v = rej.get("vendor_name") or "Unknown"
                reason = rej.get("rejection_reason", "Data Error")
                pid = rej.get("product_id") or "N/A"
                print(f"  {c_red('>>')} REJECTED | Vendor: {v} | Item: {pid} | Reason: {c_red(reason)}")

            if len(report.rejected_alerts) > 5:
                print(f"  ... and {len(report.rejected_alerts) - 5} more rejected records logged to audit table.")

        print("=" * 76 + "\n")

    def generate_html_dashboard(
        self,
        report: AlertBatchReport,
        db_summary: Optional[Dict[str, Any]] = None,
        all_inventory_items: Optional[List[Dict[str, Any]]] = None,
    ) -> Path:
        """
        Generates a standalone, interactive HTML Alert Dashboard.
        Can be opened directly in any browser for executive reviews and interviews.
        """
        all_items = all_inventory_items or report.processed_items
        db_stats = db_summary or {}

        # Precompute counts
        critical_count = sum(1 for it in all_items if it.get("quantity", 0) <= self.low_stock_threshold)
        warning_count = sum(1 for it in all_items if it.get("price", 0.0) >= self.price_spike_threshold)
        rejection_count = db_stats.get("total_rejected_records", report.total_rejections)
        total_items_count = len(all_items)

        # Build table rows HTML
        table_rows_html = []
        for idx, item in enumerate(all_items):
            qty = int(item.get("quantity", 0))
            price = float(item.get("price", 0.0))
            vendor = item.get("vendor_name", "N/A")
            product = item.get("product_name", "N/A")
            sku = item.get("sku", "N/A")
            invoice = item.get("invoice_no") or "N/A"
            source = item.get("source_file") or "Email Attachment"

            # Alert classification
            row_class = ""
            status_badge = '<span class="badge badge-success">OK / In Stock</span>'
            filter_tag = "normal"

            if qty == 0:
                row_class = "table-danger"
                status_badge = '<span class="badge badge-critical">CRITICAL: Out of Stock (0)</span>'
                filter_tag = "critical"
            elif qty <= self.low_stock_threshold:
                row_class = "table-warning-critical"
                status_badge = f'<span class="badge badge-critical">CRITICAL: Low Stock ({qty})</span>'
                filter_tag = "critical"
            elif price >= self.price_spike_threshold:
                row_class = "table-warning"
                status_badge = f'<span class="badge badge-warning">WARNING: High Value (${price:.2f})</span>'
                filter_tag = "warning"

            row_html = f"""
            <tr class="{row_class}" data-filter="{filter_tag}">
                <td data-label="#"><span class="val-text font-bold">{idx + 1}</span></td>
                <td data-label="Vendor"><span class="val-text"><span class="vendor-tag">{vendor}</span></span></td>
                <td data-label="SKU"><span class="val-text"><code class="sku-code">{sku}</code></span></td>
                <td data-label="Product"><span class="val-text font-bold">{product}</span></td>
                <td data-label="Quantity" class="text-center"><span class="val-text font-bold">{qty}</span></td>
                <td data-label="Unit Price" class="text-right"><span class="val-text font-mono">${price:.2f}</span></td>
                <td data-label="Invoice"><span class="val-text"><span class="invoice-tag">{invoice}</span></span></td>
                <td data-label="Alert Status"><span class="val-text">{status_badge}</span></td>
                <td data-label="Source"><span class="val-text"><span class="source-file" title="{source}">{source}</span></span></td>
            </tr>
            """
            table_rows_html.append(row_html)

        rendered_rows = "\n".join(table_rows_html)

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
    <title>Vendor Inventory & Alert Monitoring Dashboard</title>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --border: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --critical: #ef4444;
            --critical-bg: #450a0a;
            --warning: #f59e0b;
            --warning-bg: #451a03;
            --success: #10b981;
            --success-bg: #064e3b;
            --info: #3b82f6;
            --info-bg: #1e3a8a;
        }}
        html {{
            box-sizing: border-box;
            width: 100%;
            max-width: 100vw;
            overflow-x: hidden;
            -webkit-text-size-adjust: 100%;
        }}
        *, *::before, *::after {{
            box-sizing: inherit;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-primary);
            padding: 24px;
            line-height: 1.5;
            width: 100%;
            max-width: 100vw;
            overflow-x: hidden;
            margin: 0;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
            margin-bottom: 24px;
            width: 100%;
            max-width: 100%;
        }}
        .header-title h1 {{
            font-size: 24px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
            word-break: break-word;
        }}
        .header-title p {{
            color: var(--text-secondary);
            font-size: 13px;
            margin-top: 4px;
            word-break: break-word;
        }}
        .badge-live {{
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
            border: 1px solid var(--success);
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
            white-space: nowrap;
            display: inline-block;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
            width: 100%;
            max-width: 100%;
        }}
        .stat-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            min-width: 0;
            box-sizing: border-box;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .stat-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
        }}
        .stat-card.critical {{
            border-color: var(--critical);
            background: linear-gradient(135deg, var(--surface) 60%, var(--critical-bg) 100%);
        }}
        .stat-card.warning {{
            border-color: var(--warning);
            background: linear-gradient(135deg, var(--surface) 60%, var(--warning-bg) 100%);
        }}
        .stat-label {{
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            word-break: break-word;
        }}
        .stat-value {{
            font-size: 28px;
            font-weight: 800;
            line-height: 1.1;
        }}
        .stat-desc {{
            font-size: 12px;
            color: var(--text-secondary);
            word-break: break-word;
        }}
        .toolbar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 16px;
            background: var(--surface);
            padding: 12px 16px;
            border-radius: 10px;
            border: 1px solid var(--border);
            width: 100%;
            max-width: 100%;
        }}
        .search-box {{
            flex: 1;
            min-width: 220px;
            max-width: 100%;
        }}
        .search-box input {{
            width: 100%;
            max-width: 100%;
            padding: 9px 14px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text-primary);
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            border-color: var(--info);
        }}
        .filter-buttons {{
            display: flex;
            gap: 8px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: thin;
            padding-bottom: 4px;
            max-width: 100%;
        }}
        .filter-buttons::-webkit-scrollbar {{
            height: 4px;
        }}
        .filter-buttons::-webkit-scrollbar-thumb {{
            background: var(--border);
            border-radius: 4px;
        }}
        .btn-filter {{
            background: var(--surface-hover);
            color: var(--text-primary);
            border: 1px solid var(--border);
            padding: 8px 14px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            flex-shrink: 0;
            transition: all 0.2s;
        }}
        .btn-filter.active, .btn-filter:hover {{
            background: var(--info);
            border-color: var(--info);
            color: #fff;
        }}
        .view-toggle-btn {{
            background: var(--surface-hover);
            color: var(--text-secondary);
            border: 1px solid var(--border);
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 12px;
            cursor: pointer;
            font-weight: 600;
            white-space: nowrap;
            flex-shrink: 0;
        }}
        .table-container {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            width: 100%;
            max-width: 100%;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
            text-align: left;
        }}
        th {{
            background: #111827;
            padding: 12px 14px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
            vertical-align: middle;
        }}
        tr:hover td {{
            background: rgba(255, 255, 255, 0.03);
        }}
        tr.table-danger td {{
            background: rgba(239, 68, 68, 0.12);
        }}
        tr.table-warning-critical td {{
            background: rgba(239, 68, 68, 0.07);
        }}
        tr.table-warning td {{
            background: rgba(245, 158, 11, 0.07);
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            line-height: 1.2;
        }}
        .badge-critical {{
            background: var(--critical);
            color: #fff;
        }}
        .badge-warning {{
            background: var(--warning);
            color: #000;
        }}
        .badge-success {{
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
            border: 1px solid var(--success);
        }}
        .vendor-tag {{
            background: #334155;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 12px;
            display: inline-block;
            max-width: 100%;
            word-break: break-word;
        }}
        .invoice-tag {{
            font-family: monospace;
            background: #0f172a;
            border: 1px solid var(--border);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
            display: inline-block;
            max-width: 100%;
            word-break: break-all;
        }}
        .sku-code {{
            background: rgba(0, 0, 0, 0.25);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
            color: #38bdf8;
            font-family: monospace;
            display: inline-block;
            max-width: 100%;
            word-break: break-all;
        }}
        .source-file {{
            font-family: monospace;
            font-size: 11px;
            color: var(--text-secondary);
            display: inline-block;
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            vertical-align: middle;
        }}
        .val-text {{
            display: inline-block;
        }}
        .font-bold {{ font-weight: 700; }}
        .font-mono {{ font-family: monospace; }}
        .text-center {{ text-align: center; }}
        .text-right {{ text-align: right; }}
        .text-sm {{ font-size: 12px; }}
        .text-muted {{ color: var(--text-secondary); }}

        /* ========================================================= */
        /* DESKTOP EXPLICIT CARD-MODE STYLING                        */
        /* ========================================================= */
        .table-container.card-mode {{
            background: transparent !important;
            border: none !important;
            overflow-x: hidden !important;
            width: 100% !important;
            max-width: 100% !important;
        }}
        .table-container.card-mode table,
        .table-container.card-mode thead,
        .table-container.card-mode tbody,
        .table-container.card-mode th,
        .table-container.card-mode td,
        .table-container.card-mode tr {{
            display: block !important;
            width: 100% !important;
            max-width: 100% !important;
            box-sizing: border-box !important;
        }}
        .table-container.card-mode thead {{
            display: none !important;
        }}
        .table-container.card-mode tr {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            margin-bottom: 12px;
            padding: 12px 14px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }}
        .table-container.card-mode tr.table-danger {{
            border-color: var(--critical);
            background: linear-gradient(135deg, var(--surface) 70%, var(--critical-bg) 100%);
        }}
        .table-container.card-mode tr.table-warning-critical {{
            border-color: var(--critical);
            background: linear-gradient(135deg, var(--surface) 75%, rgba(239, 68, 68, 0.15) 100%);
        }}
        .table-container.card-mode tr.table-warning {{
            border-color: var(--warning);
            background: linear-gradient(135deg, var(--surface) 75%, var(--warning-bg) 100%);
        }}
        .table-container.card-mode td {{
            display: flex !important;
            justify-content: space-between !important;
            align-items: flex-start !important;
            gap: 10px;
            padding: 7px 0 !important;
            border: none !important;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06) !important;
            font-size: 13px !important;
            width: 100% !important;
            max-width: 100% !important;
            box-sizing: border-box !important;
        }}
        .table-container.card-mode td:last-child {{
            border-bottom: none !important;
            padding-bottom: 0 !important;
        }}
        .table-container.card-mode td::before {{
            content: attr(data-label);
            font-weight: 600;
            color: var(--text-secondary);
            text-align: left;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            flex-shrink: 0;
            max-width: 38%;
            padding-top: 2px;
        }}
        .table-container.card-mode .val-text {{
            flex: 1;
            min-width: 0;
            max-width: 62%;
            text-align: right;
            word-break: break-word;
            overflow-wrap: anywhere;
        }}
        .table-container.card-mode .source-file {{
            max-width: 100% !important;
            white-space: normal !important;
            word-break: break-all !important;
            overflow-wrap: anywhere !important;
            display: inline-block !important;
            text-align: right;
        }}

        /* ========================================================= */
        /* FULLY RESPONSIVE MOBILE & TABLET LAYOUT (<= 768px)        */
        /* ========================================================= */
        @media (max-width: 768px) {{
            body {{
                padding: 12px 10px;
            }}
            .header {{
                flex-direction: column;
                align-items: flex-start;
                gap: 8px;
                padding-bottom: 12px;
                margin-bottom: 14px;
            }}
            .header-title h1 {{
                font-size: clamp(16px, 4.5vw, 20px);
                line-height: 1.25;
            }}
            .header-title p {{
                font-size: 11px;
            }}
            .stats-grid {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 8px;
                margin-bottom: 14px;
            }}
            .stat-card {{
                padding: 10px 12px;
                border-radius: 10px;
            }}
            .stat-label {{
                font-size: 10px;
                letter-spacing: 0.03em;
            }}
            .stat-value {{
                font-size: clamp(18px, 5vw, 22px);
            }}
            .stat-desc {{
                font-size: 10px;
                line-height: 1.2;
            }}
            .toolbar {{
                flex-direction: column;
                align-items: stretch;
                padding: 10px;
                gap: 10px;
                margin-bottom: 12px;
            }}
            .search-box {{
                min-width: 0;
                width: 100%;
            }}
            .search-box input {{
                font-size: 13px;
                padding: 8px 12px;
            }}
            .filter-buttons {{
                width: 100%;
                padding-bottom: 4px;
            }}
            .btn-filter {{
                padding: 6px 10px;
                font-size: 11px;
            }}
            .view-toggle-btn {{
                padding: 6px 10px;
                font-size: 11px;
            }}

            /* Mobile default: Card View unless user selects .force-table */
            .table-container:not(.force-table) {{
                background: transparent !important;
                border: none !important;
                overflow-x: hidden !important;
                padding: 0 !important;
                width: 100% !important;
                max-width: 100% !important;
            }}
            .table-container:not(.force-table) table,
            .table-container:not(.force-table) thead,
            .table-container:not(.force-table) tbody,
            .table-container:not(.force-table) th,
            .table-container:not(.force-table) td,
            .table-container:not(.force-table) tr {{
                display: block !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
            }}
            .table-container:not(.force-table) thead {{
                display: none !important;
            }}
            .table-container:not(.force-table) tr {{
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 12px;
                margin-bottom: 12px;
                padding: 12px 14px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
            }}
            .table-container:not(.force-table) tr.table-danger {{
                border-color: var(--critical);
                background: linear-gradient(135deg, var(--surface) 70%, var(--critical-bg) 100%);
            }}
            .table-container:not(.force-table) tr.table-warning-critical {{
                border-color: var(--critical);
                background: linear-gradient(135deg, var(--surface) 75%, rgba(239, 68, 68, 0.15) 100%);
            }}
            .table-container:not(.force-table) tr.table-warning {{
                border-color: var(--warning);
                background: linear-gradient(135deg, var(--surface) 75%, var(--warning-bg) 100%);
            }}
            .table-container:not(.force-table) td {{
                display: flex !important;
                justify-content: space-between !important;
                align-items: flex-start !important;
                gap: 10px;
                padding: 7px 0 !important;
                border: none !important;
                border-bottom: 1px solid rgba(255, 255, 255, 0.06) !important;
                font-size: 13px !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
            }}
            .table-container:not(.force-table) td:last-child {{
                border-bottom: none !important;
                padding-bottom: 0 !important;
            }}
            .table-container:not(.force-table) td::before {{
                content: attr(data-label);
                font-weight: 600;
                color: var(--text-secondary);
                text-align: left;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.04em;
                flex-shrink: 0;
                max-width: 38%;
                padding-top: 2px;
            }}
            .table-container:not(.force-table) .val-text {{
                flex: 1;
                min-width: 0;
                max-width: 62%;
                text-align: right;
                word-break: break-word;
                overflow-wrap: anywhere;
            }}
            .table-container:not(.force-table) .source-file {{
                max-width: 100% !important;
                white-space: normal !important;
                word-break: break-all !important;
                overflow-wrap: anywhere !important;
                display: inline-block !important;
                text-align: right;
            }}

            /* Explicit Table View on Mobile */
            .table-container.force-table {{
                background: var(--surface) !important;
                border: 1px solid var(--border) !important;
                border-radius: 12px !important;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
            }}
            .table-container.force-table table {{
                min-width: 680px !important;
                width: 100% !important;
                display: table !important;
            }}
            .table-container.force-table thead {{
                display: table-header-group !important;
            }}
            .table-container.force-table tbody {{
                display: table-row-group !important;
            }}
            .table-container.force-table tr {{
                display: table-row !important;
                margin-bottom: 0 !important;
                padding: 0 !important;
                border: none !important;
                box-shadow: none !important;
            }}
            .table-container.force-table th {{
                display: table-cell !important;
                padding: 10px 12px !important;
            }}
            .table-container.force-table td {{
                display: table-cell !important;
                padding: 10px 12px !important;
                border-bottom: 1px solid var(--border) !important;
            }}
            .table-container.force-table td::before {{
                display: none !important;
            }}
            .table-container.force-table .val-text {{
                display: inline !important;
                max-width: 100% !important;
            }}
        }}

        @media (max-width: 340px) {{
            .stats-grid {{
                grid-template-columns: 1fr;
            }}
            body {{
                padding: 8px 6px;
            }}
        }}
    </style>
</head>
<body>

    <header class="header">
        <div class="header-title">
            <h1>Email Inventory & Anomaly Detection System</h1>
            <p>Automated vendor ingestion pipeline &bull; Last synchronized: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
        <div>
            <span class="badge-live">&#9679; LIVE MONITOR</span>
        </div>
    </header>

    <section class="stats-grid">
        <div class="stat-card">
            <span class="stat-label">Total Stock Items</span>
            <span class="stat-value">{total_items_count}</span>
            <span class="stat-desc">Distinct inventory products</span>
        </div>
        <div class="stat-card critical">
            <span class="stat-label" style="color: var(--critical);">Critical Shortages</span>
            <span class="stat-value" style="color: var(--critical);">{critical_count}</span>
            <span class="stat-desc">Stock &le; {self.low_stock_threshold} units</span>
        </div>
        <div class="stat-card warning">
            <span class="stat-label" style="color: var(--warning);">Price Warnings</span>
            <span class="stat-value" style="color: var(--warning);">{warning_count}</span>
            <span class="stat-desc">Unit price &ge; ${self.price_spike_threshold:.2f}</span>
        </div>
        <div class="stat-card">
            <span class="stat-label">Rejected Records</span>
            <span class="stat-value">{rejection_count}</span>
            <span class="stat-desc">Data quality audit violations</span>
        </div>
    </section>

    <div class="toolbar">
        <div class="search-box">
            <input type="text" id="searchInput" placeholder="Search by SKU, Product Name, Vendor, or Invoice #...">
        </div>
        <div class="filter-buttons">
            <button class="btn-filter active" onclick="setFilter('all', this)">All ({total_items_count})</button>
            <button class="btn-filter" onclick="setFilter('critical', this)">Critical ({critical_count})</button>
            <button class="btn-filter" onclick="setFilter('warning', this)">Warnings ({warning_count})</button>
            <button class="btn-filter" onclick="setFilter('normal', this)">Healthy</button>
            <button class="view-toggle-btn" id="viewToggleBtn" onclick="toggleViewMode()">📱 Card View</button>
        </div>
    </div>

    <div class="table-container" id="tableContainer">
        <table id="inventoryTable">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Vendor</th>
                    <th>SKU</th>
                    <th>Product Description</th>
                    <th class="text-center">Quantity</th>
                    <th class="text-right">Unit Price</th>
                    <th>Invoice / PO</th>
                    <th>Alert Status</th>
                    <th>Source Attachment</th>
                </tr>
            </thead>
            <tbody>
                {rendered_rows}
            </tbody>
        </table>
    </div>

    <script>
        let currentFilter = 'all';

        function updateToggleBtnLabel() {{
            const btn = document.getElementById('viewToggleBtn');
            const container = document.getElementById('tableContainer');
            const isMobile = window.innerWidth <= 768;

            if (isMobile) {{
                if (container.classList.contains('force-table')) {{
                    btn.innerHTML = '📱 Card View';
                }} else {{
                    btn.innerHTML = '📊 Table View';
                }}
            }} else {{
                if (container.classList.contains('card-mode')) {{
                    btn.innerHTML = '📊 Table View';
                }} else {{
                    btn.innerHTML = '📱 Card View';
                }}
            }}
        }}

        function toggleViewMode() {{
            const container = document.getElementById('tableContainer');
            const isMobile = window.innerWidth <= 768;

            if (isMobile) {{
                container.classList.toggle('force-table');
            }} else {{
                container.classList.toggle('card-mode');
            }}
            updateToggleBtnLabel();
        }}

        function setFilter(filter, el) {{
            currentFilter = filter;
            document.querySelectorAll('.btn-filter').forEach(btn => btn.classList.remove('active'));
            if (el) el.classList.add('active');
            applyFilters();
        }}

        function applyFilters() {{
            const search = document.getElementById('searchInput').value.toLowerCase();
            const rows = document.querySelectorAll('#inventoryTable tbody tr');

            rows.forEach(row => {{
                const filterTag = row.getAttribute('data-filter');
                const text = row.innerText.toLowerCase();

                const matchesFilter = (currentFilter === 'all') || (filterTag === currentFilter);
                const matchesSearch = text.includes(search);

                if (matchesFilter && matchesSearch) {{
                    row.style.display = '';
                }} else {{
                    row.style.display = 'none';
                }}
            }});
        }}

        document.getElementById('searchInput').addEventListener('keyup', applyFilters);
        window.addEventListener('resize', updateToggleBtnLabel);
        updateToggleBtnLabel();
    </script>
</body>
</html>
"""
        with open(self.dashboard_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info("Saved Interactive HTML Alert Dashboard to '%s'", self.dashboard_path.name)
        return self.dashboard_path

    def open_dashboard_in_browser(self) -> None:
        """Opens the generated dashboard in the system default browser."""
        if self.dashboard_path.exists():
            webbrowser.open(self.dashboard_path.resolve().as_uri())
        else:
            logger.warning("Dashboard file not found at '%s'", self.dashboard_path)

