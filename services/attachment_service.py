"""
Attachment Service Module
Handles inspection, format validation, SHA-256 deduplication,
filename sanitization, and secure saving of email attachments.
"""

import base64
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from config.settings import (
    ALLOWED_EXTENSIONS,
    DOWNLOADS_DIR,
    MAX_FILE_SIZE_BYTES,
)

logger = logging.getLogger("inventory_automation")


@dataclass
class AttachmentInfo:
    """Represents a downloaded attachment's metadata."""
    original_filename: str
    saved_path: Path
    file_hash: str
    size_bytes: int
    extension: str
    is_valid_format: bool
    is_empty: bool


class AttachmentService:
    """
    Service responsible for extracting, validating, and persisting attachments.
    """

    def __init__(self, download_dir: Path = DOWNLOADS_DIR):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Strips path traversal sequences and invalid characters for Windows/Linux filesystems.
        """
        # Remove path components
        base_name = Path(filename).name
        # Replace non-alphanumeric (except dots, dashes, underscores) with underscore
        clean_name = re.sub(r"[^A-Za-z0-9_.-]", "_", base_name)
        # Collapse multiple underscores
        clean_name = re.sub(r"_+", "_", clean_name)
        return clean_name or "unnamed_attachment"

    @staticmethod
    def compute_sha256(content: bytes) -> str:
        """Calculates SHA-256 hash of byte content."""
        hasher = hashlib.sha256()
        hasher.update(content)
        return hasher.hexdigest()

    def validate_attachment(self, filename: str, content_size: int) -> Tuple[bool, Optional[str]]:
        """
        Validates file extension and size constraints.
        Returns (is_valid, rejection_reason).
        """
        extension = Path(filename).suffix.lower()

        if not extension:
            return False, "MISSING_FILE_EXTENSION"

        if extension not in ALLOWED_EXTENSIONS:
            return False, f"UNSUPPORTED_FORMAT: {extension} (Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))})"

        if content_size == 0:
            return False, "EMPTY_ATTACHMENT"

        if content_size > MAX_FILE_SIZE_BYTES:
            mb_size = content_size / (1024 * 1024)
            limit_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
            return False, f"FILE_SIZE_EXCEEDED: {mb_size:.2f}MB exceeds {limit_mb:.2f}MB limit"

        return True, None

    def save_attachment_bytes(
        self,
        raw_bytes: bytes,
        original_filename: str,
        vendor_prefix: Optional[str] = None,
    ) -> AttachmentInfo:
        """
        Saves raw bytes to disk with timestamped, sanitized name and returns metadata.
        """
        sanitized_original = self.sanitize_filename(original_filename)
        extension = Path(sanitized_original).suffix.lower()
        size_bytes = len(raw_bytes)
        file_hash = self.compute_sha256(raw_bytes)

        is_empty = size_bytes == 0
        is_valid_format = extension in ALLOWED_EXTENSIONS and not is_empty

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"{self.sanitize_filename(vendor_prefix)}_" if vendor_prefix else ""
        unique_name = f"{timestamp}_{prefix}{sanitized_original}"
        saved_path = self.download_dir / unique_name

        # Persist to disk
        with open(saved_path, "wb") as f:
            f.write(raw_bytes)

        logger.info(
            "Saved attachment '%s' to '%s' (%d bytes, hash: %s...)",
            original_filename,
            saved_path.name,
            size_bytes,
            file_hash[:8],
        )

        return AttachmentInfo(
            original_filename=original_filename,
            saved_path=saved_path,
            file_hash=file_hash,
            size_bytes=size_bytes,
            extension=extension,
            is_valid_format=is_valid_format,
            is_empty=is_empty,
        )

    def extract_and_save_from_gmail_part(
        self,
        part: Dict[str, Any],
        gmail_client_or_service: Any,
        message_id: str,
        vendor_prefix: Optional[str] = None,
    ) -> Optional[AttachmentInfo]:
        """
        Extracts an attachment from a Gmail message part and saves it to disk.
        Supports both inline base64 and remote attachmentId retrieval.
        """
        filename = part.get("filename", "")
        if not filename:
            return None

        body = part.get("body", {})
        data_base64 = body.get("data")
        attachment_id = body.get("attachmentId")
        raw_bytes: bytes = b""

        if data_base64:
            # Inline data
            raw_bytes = base64.urlsafe_b64decode(data_base64.encode("UTF-8"))
        elif attachment_id and gmail_client_or_service:
            # Remote attachment payload
            logger.debug("Fetching attachmentId '%s' for message '%s'", attachment_id, message_id)
            attachment_part = (
                gmail_client_or_service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            raw_data = attachment_part.get("data", "")
            if raw_data:
                raw_bytes = base64.urlsafe_b64decode(raw_data.encode("UTF-8"))
        else:
            logger.warning("No data found for attachment '%s' in message '%s'", filename, message_id)
            return None

        return self.save_attachment_bytes(
            raw_bytes=raw_bytes,
            original_filename=filename,
            vendor_prefix=vendor_prefix,
        )
