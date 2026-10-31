"""
Inventory Data Processor Module
Reads CSV and Excel attachments using Pandas, normalizes columns,
validates types and business constraints, removes duplicates,
and separates valid records from rejected rows with explicit reasons.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config.settings import (
    CANONICAL_COLUMNS,
    COLUMN_ALIASES,
    MANDATORY_COLUMNS,
)

logger = logging.getLogger("inventory_automation")


@dataclass
class ProcessingResult:
    """Detailed summary of inventory file processing."""
    file_path: Path
    total_raw_rows: int = 0
    valid_count: int = 0
    rejected_count: int = 0
    valid_records: List[Dict[str, Any]] = field(default_factory=list)
    rejected_records: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    success: bool = True


class InventoryProcessor:
    """
    Cleans, normalizes, and validates vendor inventory files.
    Supports CSV, Excel (.xlsx, .xls), PDF invoices/catalogs, and delimited text (.txt, .tsv).
    """

    def __init__(
        self,
        canonical_columns: Optional[List[str]] = None,
        mandatory_columns: Optional[List[str]] = None,
    ):
        self.canonical_columns = canonical_columns or CANONICAL_COLUMNS
        self.mandatory_columns = mandatory_columns or MANDATORY_COLUMNS

    def read_pdf(self, file_path: Path) -> pd.DataFrame:
        """
        Parses PDF attachments (invoices, purchase orders, catalog sheets)
        using pypdf and extracts tabular data rows into a DataFrame.
        """
        try:
            import pypdf
        except ImportError:
            raise ImportError("pypdf is required to process PDF attachments. Please install via: pip install pypdf")

        path = Path(file_path)
        reader = pypdf.PdfReader(str(path))
        if len(reader.pages) == 0:
            raise ValueError(f"PDF file '{path.name}' contains 0 pages.")

        extracted_text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                extracted_text += t + "\n"

        if not extracted_text.strip():
            raise ValueError(f"No extractable text found in PDF '{path.name}'.")

        # Extract Invoice Number if present in header/metadata
        invoice_no = None
        inv_match = re.search(r"(?:invoice|inv|bill|po)\s*(?:no|number|#)?[:\s]+([A-Za-z0-9\-_]+)", extracted_text, re.IGNORECASE)
        if inv_match:
            invoice_no = inv_match.group(1).strip()

        lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]

        # Try to locate header row
        header_idx = -1
        header_cols = []
        delimiter = None

        keywords = ["sku", "product", "item", "qty", "quantity", "price", "description", "unit", "amount"]
        for idx, line in enumerate(lines):
            lower_line = line.lower()
            matched_keywords = [k for k in keywords if k in lower_line]
            if len(matched_keywords) >= 2:
                # Detect delimiter
                if "|" in line:
                    delimiter = "|"
                    header_cols = [c.strip() for c in line.split("|") if c.strip()]
                elif "\t" in line:
                    delimiter = "\t"
                    header_cols = [c.strip() for c in line.split("\t") if c.strip()]
                elif "," in line:
                    delimiter = ","
                    header_cols = [c.strip() for c in line.split(",") if c.strip()]
                else:
                    parts = re.split(r"\s{2,}", line)
                    if len(parts) >= 3:
                        delimiter = r"\s{2,}"
                        header_cols = [p.strip() for p in parts if p.strip()]

                if header_cols and len(header_cols) >= 3:
                    header_idx = idx
                    break

        rows = []
        if header_idx != -1 and header_cols:
            for line in lines[header_idx + 1:]:
                if re.search(r"^(total|subtotal|notes|terms|page\s+\d+|thank you)", line, re.IGNORECASE):
                    continue
                if delimiter in ("|", "\t", ","):
                    parts = [p.strip() for p in line.split(delimiter) if p.strip()]
                else:
                    parts = [p.strip() for p in re.split(delimiter, line) if p.strip()]

                if len(parts) == len(header_cols):
                    rows.append(dict(zip(header_cols, parts)))
                elif len(parts) >= 3:
                    if len(parts) < len(header_cols):
                        parts.extend([""] * (len(header_cols) - len(parts)))
                    rows.append(dict(zip(header_cols, parts[:len(header_cols)])))

        # Fallback: line-based regex extraction if table delimiter split yielded 0 rows
        if not rows:
            row_pattern = re.compile(
                r"^([A-Za-z0-9\-_]{2,15})\s+([A-Za-z0-9\s\-_()]{3,40}?)\s+([A-Za-z0-9\-_]{2,20})\s+(\d+)\s+[\$€£]?(\d+(?:\.\d{1,2})?)$"
            )
            for line in lines:
                m = row_pattern.match(line)
                if m:
                    rows.append({
                        "product_id": m.group(1),
                        "product_name": m.group(2).strip(),
                        "sku": m.group(3),
                        "quantity": m.group(4),
                        "price": m.group(5),
                    })

        if not rows:
            raise ValueError(f"Could not extract tabular product records from PDF '{path.name}'.")

        df = pd.DataFrame(rows)
        if invoice_no and "invoice_no" not in df.columns:
            df["invoice_no"] = invoice_no

        logger.debug("Successfully parsed %d rows from PDF '%s'", len(df), path.name)
        return df

    def read_delimited_text(self, file_path: Path) -> pd.DataFrame:
        """
        Loads tab, pipe, or comma-delimited text/tsv files.
        """
        path = Path(file_path)
        try:
            df = pd.read_csv(path, sep=None, engine="python", dtype=str)
            logger.debug("Successfully read delimited text file '%s'", path.name)
            return df
        except Exception as exc:
            raise ValueError(f"Failed to parse delimited text file '{path.name}': {exc}")

    def read_file(self, file_path: Path) -> pd.DataFrame:
        """
        Safely loads a CSV, Excel, PDF, or delimited text file into a Pandas DataFrame.
        Handles various encodings and corrupted/empty file states.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if path.stat().st_size == 0:
            raise ValueError(f"File is empty (0 bytes): {path.name}")

        ext = path.suffix.lower()

        if ext == ".csv":
            encodings = ["utf-8", "utf-8-sig", "latin1", "cp1252", "iso-8859-1"]
            last_err = None
            for enc in encodings:
                try:
                    df = pd.read_csv(path, encoding=enc, dtype=str, skipinitialspace=True)
                    logger.debug("Successfully read CSV '%s' with encoding '%s'", path.name, enc)
                    return df
                except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                    last_err = exc
                    continue
            raise ValueError(f"Unable to parse CSV file '{path.name}' with standard encodings: {last_err}")

        elif ext in (".xlsx", ".xls"):
            try:
                # Read all values as strings initially to prevent premature truncation/corruption
                df = pd.read_excel(path, dtype=str)
                logger.debug("Successfully read Excel file '%s'", path.name)
                return df
            except Exception as exc:
                raise ValueError(f"Failed to read Excel file '{path.name}': {exc}")

        elif ext == ".pdf":
            return self.read_pdf(path)

        elif ext in (".txt", ".tsv"):
            return self.read_delimited_text(path)

        else:
            raise ValueError(f"Unsupported file format '{ext}' for file '{path.name}'")

    @staticmethod
    def _normalize_header(header: str) -> str:
        """
        Strips whitespace and converts header string to clean lowercase snake_case.
        """
        cleaned = str(header).strip().lower()
        cleaned = re.sub(r"[\s\-]+", "_", cleaned)
        cleaned = re.sub(r"[^\w_]", "", cleaned)
        return cleaned

    def normalize_columns(
        self,
        df: pd.DataFrame,
        fallback_vendor_name: Optional[str] = None,
    ) -> Tuple[pd.DataFrame, Dict[str, str]]:
        """
        Maps source DataFrame column names to standard canonical names.
        """
        source_cols = list(df.columns)
        mapping: Dict[str, str] = {}

        # Build alias lookup table
        alias_to_canonical: Dict[str, str] = {}
        for canonical, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                norm_alias = self._normalize_header(alias)
                alias_to_canonical[norm_alias] = canonical

        # Map each column in DataFrame
        for col in source_cols:
            norm_col = self._normalize_header(col)
            if norm_col in alias_to_canonical:
                canonical_target = alias_to_canonical[norm_col]
                # Only map if not already mapped to another column
                if canonical_target not in mapping.values():
                    mapping[col] = canonical_target

        renamed_df = df.rename(columns=mapping)

        # If vendor_name is absent in headers but we have a fallback vendor name from sender
        if "vendor_name" not in renamed_df.columns and fallback_vendor_name:
            renamed_df["vendor_name"] = fallback_vendor_name
            mapping["[INFERRED]"] = "vendor_name"

        # If updated_date is missing, default to today's ISO date
        if "updated_date" not in renamed_df.columns:
            today_str = datetime.now().strftime("%Y-%m-%d")
            renamed_df["updated_date"] = today_str
            mapping["[DEFAULT_TODAY]"] = "updated_date"

        # If invoice_no is missing, default to empty string
        if "invoice_no" not in renamed_df.columns:
            renamed_df["invoice_no"] = ""
            mapping["[DEFAULT_EMPTY]"] = "invoice_no"

        return renamed_df, mapping

    def check_required_columns(self, df: pd.DataFrame) -> List[str]:
        """
        Returns list of mandatory canonical columns missing from the DataFrame.
        """
        return [col for col in self.mandatory_columns if col not in df.columns]

    @staticmethod
    def clean_price(val: Any) -> Tuple[Optional[float], Optional[str]]:
        """
        Cleans and parses price value.
        Strips '$', '€', '£', commas, and handles decimal parsing.
        """
        if pd.isna(val) or val is None:
            return None, "PRICE_IS_NULL"

        val_str = str(val).strip()
        if not val_str:
            return None, "PRICE_IS_EMPTY"

        # Remove currency symbols and comma separators
        cleaned = re.sub(r"[^\d.-]", "", val_str)

        try:
            price_float = float(cleaned)
            if price_float < 0:
                return None, f"NEGATIVE_PRICE: {val_str}"
            return round(price_float, 2), None
        except ValueError:
            return None, f"INVALID_PRICE_FORMAT: '{val_str}'"

    @staticmethod
    def clean_quantity(val: Any) -> Tuple[Optional[int], Optional[str]]:
        """
        Cleans and parses quantity value into non-negative integer.
        """
        if pd.isna(val) or val is None:
            return None, "QUANTITY_IS_NULL"

        val_str = str(val).strip()
        if not val_str:
            return None, "QUANTITY_IS_EMPTY"

        # Remove commas or trailing .0 from float string (e.g., '10.0')
        val_str = val_str.replace(",", "")
        try:
            qty_float = float(val_str)
            if not qty_float.is_integer():
                return None, f"FRACTIONAL_QUANTITY: '{val_str}'"
            qty_int = int(qty_float)
            if qty_int < 0:
                return None, f"NEGATIVE_QUANTITY: {qty_int}"
            return qty_int, None
        except ValueError:
            return None, f"INVALID_QUANTITY_FORMAT: '{val_str}'"

    @staticmethod
    def clean_date(val: Any) -> Tuple[Optional[str], Optional[str]]:
        """
        Parses and standardizes date into YYYY-MM-DD string.
        """
        if pd.isna(val) or val is None:
            return datetime.now().strftime("%Y-%m-%d"), None

        val_str = str(val).strip()
        if not val_str:
            return datetime.now().strftime("%Y-%m-%d"), None

        # Common format trials
        try:
            dt = pd.to_datetime(val_str, errors="coerce")
            if pd.isna(dt):
                return None, f"INVALID_DATE_FORMAT: '{val_str}'"
            return dt.strftime("%Y-%m-%d"), None
        except Exception:
            return None, f"INVALID_DATE_PARSE: '{val_str}'"

    def process_file(
        self,
        file_path: Path,
        fallback_vendor_name: Optional[str] = None,
    ) -> ProcessingResult:
        """
        End-to-end processing pipeline for a single inventory file.
        Returns ProcessingResult containing valid records and rejected audit entries.
        """
        result = ProcessingResult(file_path=file_path)

        try:
            raw_df = self.read_file(file_path)
        except Exception as exc:
            logger.error("Failed to read file '%s': %s", file_path.name, exc)
            result.success = False
            result.error_message = str(exc)
            return result

        result.total_raw_rows = len(raw_df)
        if result.total_raw_rows == 0:
            result.success = False
            result.error_message = f"File '{file_path.name}' contains headers but 0 data rows."
            return result

        # Normalize column names
        df, _ = self.normalize_columns(raw_df, fallback_vendor_name=fallback_vendor_name)

        # Check for mandatory columns
        missing_cols = self.check_required_columns(df)
        if missing_cols:
            error_msg = f"Missing required canonical columns: {', '.join(missing_cols)}"
            logger.warning("File '%s' rejected: %s", file_path.name, error_msg)
            result.success = False
            result.error_message = error_msg
            return result

        # Record validation
        seen_keys: set = set()
        valid_records: List[Dict[str, Any]] = []
        rejected_records: List[Dict[str, Any]] = []

        for row_idx, row in df.iterrows():
            raw_data_str = str(row.to_dict())
            vendor_name = str(row.get("vendor_name", "")).strip()
            product_id = str(row.get("product_id", "")).strip()
            product_name = str(row.get("product_name", "")).strip()
            sku = str(row.get("sku", "")).strip()
            raw_qty = row.get("quantity")
            raw_price = row.get("price")
            raw_date = row.get("updated_date")

            rejection_reasons: List[str] = []

            # 1. String presence checks
            if not vendor_name or vendor_name.lower() in ("nan", "none", "null"):
                rejection_reasons.append("MISSING_VENDOR_NAME")
            if not product_id or product_id.lower() in ("nan", "none", "null"):
                rejection_reasons.append("MISSING_PRODUCT_ID")
            if not product_name or product_name.lower() in ("nan", "none", "null"):
                rejection_reasons.append("MISSING_PRODUCT_NAME")
            if not sku or sku.lower() in ("nan", "none", "null"):
                rejection_reasons.append("MISSING_SKU")

            # 2. Quantity validation
            clean_qty, qty_err = self.clean_quantity(raw_qty)
            if qty_err:
                rejection_reasons.append(qty_err)

            # 3. Price validation
            clean_price_val, price_err = self.clean_price(raw_price)
            if price_err:
                rejection_reasons.append(price_err)

            # 4. Date validation
            clean_dt_str, date_err = self.clean_date(raw_date)
            if date_err:
                rejection_reasons.append(date_err)

            # 5. Row deduplication within the file (vendor_name + product_id)
            dedup_key = (vendor_name.lower(), product_id.lower())
            if not rejection_reasons:
                if dedup_key in seen_keys:
                    rejection_reasons.append(f"DUPLICATE_ROW_IN_FILE: ({vendor_name}, {product_id})")
                else:
                    seen_keys.add(dedup_key)

            # Route to Valid or Rejected
            if rejection_reasons:
                rejected_records.append({
                    "row_index": row_idx + 1,
                    "vendor_name": vendor_name,
                    "product_id": product_id,
                    "raw_data": raw_data_str,
                    "rejection_reason": "; ".join(rejection_reasons),
                })
            else:
                valid_records.append({
                    "vendor_name": vendor_name,
                    "product_id": product_id,
                    "product_name": product_name,
                    "sku": sku,
                    "quantity": clean_qty,
                    "price": clean_price_val,
                    "updated_date": clean_dt_str,
                    "invoice_no": str(row.get("invoice_no") or "").strip() or None,
                })

        result.valid_count = len(valid_records)
        result.rejected_count = len(rejected_records)
        result.valid_records = valid_records
        result.rejected_records = rejected_records
        result.success = True

        logger.info(
            "Processed '%s': %d valid records, %d rejected rows (out of %d raw rows)",
            file_path.name,
            result.valid_count,
            result.rejected_count,
            result.total_raw_rows,
        )

        return result
