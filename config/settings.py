"""
Centralized Configuration & Settings Module
Vendor Inventory Email Automation System
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Dict, Set

# Attempt to load dotenv if available
try:
    from dotenv import load_dotenv
    # Load .env from project root
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()
except ImportError:
    pass

# Base Project Directories
BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"
DOWNLOADS_DIR: Path = Path(os.getenv("DOWNLOADS_DIR", DATA_DIR / "downloads"))
PROCESSED_DIR: Path = Path(os.getenv("PROCESSED_DIR", DATA_DIR / "processed"))
SAMPLES_DIR: Path = DATA_DIR / "samples"
LOGS_DIR: Path = Path(os.getenv("LOG_DIR", BASE_DIR / "logs"))
DATABASE_DIR: Path = BASE_DIR / "database"
DATABASE_PATH: Path = Path(os.getenv("DATABASE_PATH", DATABASE_DIR / "inventory.db"))
REPORTS_DIR: Path = BASE_DIR / "reports"
DASHBOARD_HTML_PATH: Path = REPORTS_DIR / "alert_dashboard.html"
REJECTED_DIR: Path = Path(os.getenv("REJECTED_DIR", DATA_DIR / "rejected"))

# Ensure essential directories exist
for directory in [DOWNLOADS_DIR, PROCESSED_DIR, REJECTED_DIR, SAMPLES_DIR, LOGS_DIR, DATABASE_DIR, REPORTS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# Gmail API Configuration
GMAIL_CREDENTIALS_PATH: Path = Path(os.getenv("GMAIL_CREDENTIALS_PATH", BASE_DIR / "credentials.json"))
GMAIL_TOKEN_PATH: Path = Path(os.getenv("GMAIL_TOKEN_PATH", BASE_DIR / "token.json"))

# Minimum required scopes for reading emails and updating read/label status
GMAIL_SCOPES: List[str] = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Email Filtering Settings
DEFAULT_KEYWORDS = [
    "inventory", "stock", "catalog", "supply", "supplies", "item list",
    "inventory update", "product", "products", "product details", "submission",
    "details", "invoice", "bill", "pricing", "price list", "quotation", "rate", "sku",
    "vendor details", "order", "items"
]
_env_keywords = os.getenv("GMAIL_SEARCH_KEYWORDS")
GMAIL_SEARCH_KEYWORDS: List[str] = (
    [k.strip().lower() for k in _env_keywords.split(",") if k.strip()]
    if _env_keywords
    else DEFAULT_KEYWORDS
)

_env_vendors = os.getenv("REGISTERED_VENDOR_EMAILS")
REGISTERED_VENDOR_EMAILS: Set[str] = (
    {email.strip().lower() for email in _env_vendors.split(",") if email.strip()}
    if _env_vendors
    else set()
)

GMAIL_MARK_AS_READ: bool = os.getenv("GMAIL_MARK_AS_READ", "true").lower() in ("true", "1", "yes")
GMAIL_APPLY_LABEL: str = os.getenv("GMAIL_APPLY_LABEL", "INVENTORY_PROCESSED")
GMAIL_BATCH_SIZE: int = int(os.getenv("GMAIL_BATCH_SIZE", "25"))

# Notification Configuration (Module C)
ENABLE_DESKTOP_NOTIFICATIONS: bool = os.getenv("ENABLE_DESKTOP_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
ENABLE_EMAIL_NOTIFICATIONS: bool = os.getenv("ENABLE_EMAIL_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
NOTIFICATION_ALERT_EMAIL: str = os.getenv("NOTIFICATION_ALERT_EMAIL", "")

# Email Priority Classification Rules (Module B)
DEFAULT_URGENT_KEYWORDS = ["urgent", "immediate", "critical", "stockout", "shortage", "asap", "emergency", "alert"]
DEFAULT_HIGH_PRIORITY_KEYWORDS = ["inventory update", "stock update", "new stock", "price increase", "revised catalog", "invoice", "bill"]
DEFAULT_PROMOTIONAL_KEYWORDS = ["promotion", "discount", "offer", "sale", "newsletter", "webinar", "deals", "coupon", "marketing", "unsubscribe"]

URGENT_KEYWORDS: List[str] = [
    k.strip().lower()
    for k in os.getenv("URGENT_KEYWORDS", ",".join(DEFAULT_URGENT_KEYWORDS)).split(",")
    if k.strip()
]
HIGH_PRIORITY_KEYWORDS: List[str] = [
    k.strip().lower()
    for k in os.getenv("HIGH_PRIORITY_KEYWORDS", ",".join(DEFAULT_HIGH_PRIORITY_KEYWORDS)).split(",")
    if k.strip()
]
PROMOTIONAL_KEYWORDS: List[str] = [
    k.strip().lower()
    for k in os.getenv("PROMOTIONAL_KEYWORDS", ",".join(DEFAULT_PROMOTIONAL_KEYWORDS)).split(",")
    if k.strip()
]

# File Processing Constraints
ALLOWED_EXTENSIONS: Set[str] = {
    ext.strip().lower()
    for ext in os.getenv("ALLOWED_EXTENSIONS", ".csv,.xlsx,.xls,.pdf,.txt,.tsv").split(",")
    if ext.strip()
}
MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "25"))
MAX_FILE_SIZE_BYTES: int = MAX_FILE_SIZE_MB * 1024 * 1024

# Alert & Highlighting Thresholds
LOW_STOCK_ALERT_THRESHOLD: int = int(os.getenv("LOW_STOCK_ALERT_THRESHOLD", "10"))
PRICE_SPIKE_ALERT_THRESHOLD: float = float(os.getenv("PRICE_SPIKE_ALERT_THRESHOLD", "500.00"))

# Scheduler Settings
SCHEDULE_INTERVAL_MINUTES: int = int(os.getenv("SCHEDULE_INTERVAL_MINUTES", "15"))
SCHEDULE_LOCK_FILE: Path = BASE_DIR / os.getenv("SCHEDULE_LOCK_FILE", "scheduler.lock")

# Canonical Field Names for Inventory Processing
MANDATORY_COLUMNS = [
    "vendor_name",
    "product_id",
    "product_name",
    "sku",
    "quantity",
    "price",
]

CANONICAL_COLUMNS = MANDATORY_COLUMNS + [
    "updated_date",
    "invoice_no",
]

# Flexible Column Aliases for Header Normalization
COLUMN_ALIASES: Dict[str, List[str]] = {
    "vendor_name": [
        "vendor", "vendor_name", "vendor name", "supplier", "supplier_name",
        "supplier name", "company", "vendor company"
    ],
    "product_id": [
        "product_id", "product id", "item_id", "item id", "id", "item_no",
        "item number", "product code", "product_code", "pid", "code"
    ],
    "product_name": [
        "product_name", "product name", "item_name", "item name", "title",
        "description", "product description", "item title", "product", "item"
    ],
    "sku": [
        "sku", "stock keeping unit", "barcode", "sku_code", "sku code",
        "part_number", "part number", "part_no", "model_number", "model"
    ],
    "quantity": [
        "quantity", "qty", "stock", "stock_quantity", "available_quantity",
        "units", "count", "amount", "inventory_qty", "on_hand"
    ],
    "price": [
        "price", "unit_price", "unit price", "cost", "unit cost", "rate",
        "price_usd", "wholesale_price", "list_price", "amount_usd"
    ],
    "updated_date": [
        "updated_date", "update_date", "updated date", "date", "last_updated",
        "last updated", "timestamp", "modified_date", "effective_date"
    ],
    "invoice_no": [
        "invoice_no", "invoice", "invoice number", "inv_no", "inv_num", "bill_no",
        "bill_number", "bill", "invoice#", "invoice_id", "po_number", "po_no"
    ],
}


def setup_logging(
    log_level: str = None,
    log_dir: Path = LOGS_DIR,
    log_filename: str = "automation.log",
) -> logging.Logger:
    """
    Configures robust application-wide logging with both RotatingFileHandler
    and StreamHandler with clean formatting.
    """
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    numeric_level = getattr(logging, log_level, logging.INFO)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / log_filename

    # Root logger configuration
    logger = logging.getLogger("inventory_automation")
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers on re-initialization
    if logger.handlers:
        return logger

    # Log formatters
    file_formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)8s] [%(name)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1. Rotating File Handler (10MB max, 5 backups, UTF-8 encoded)
    max_bytes = int(os.getenv("LOG_FILE_MAX_BYTES", 10 * 1024 * 1024))
    backup_count = int(os.getenv("LOG_FILE_BACKUP_COUNT", 5))
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # 2. Console Stream Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    return logger
