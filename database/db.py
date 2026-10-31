"""
Database Engine & Connection Manager
Smart Vendor Email & Inventory Automation System
"""

import sqlite3
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import DATABASE_PATH

logger = logging.getLogger("inventory_automation")
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@contextmanager
def get_db(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that yields a SQLite connection configured with:
    - Foreign keys enabled
    - Row factory enabled (dict-like row access)
    - Clean automatic closing on exit
    """
    target_path = Path(db_path or DATABASE_PATH)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        yield conn
    finally:
        conn.close()


def init_database(db_path: Optional[Path] = None) -> None:
    """
    Initializes database schema from database/schema.sql.
    """
    target_path = Path(db_path or DATABASE_PATH)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Database schema file not found at: {SCHEMA_PATH}")

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    with get_db(target_path) as conn:
        conn.executescript(schema_sql)
        conn.commit()

    logger.debug("Database initialized successfully at: %s", target_path)


if __name__ == "__main__":
    init_database()
    print("Database schema successfully verified and initialized.")
