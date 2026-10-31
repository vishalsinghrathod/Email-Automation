"""
Unit Tests for AttachmentService
Tests attachment validation, filename sanitization, SHA256 hashing, and disk storage.
"""

import tempfile
import unittest
from pathlib import Path

from services.attachment_service import AttachmentService


class TestAttachmentService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.download_dir = Path(self.temp_dir.name)
        self.service = AttachmentService(self.download_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sanitize_filename(self):
        dirty = "../../etc/passwd/vendor:inventory*test?.csv"
        clean = self.service.sanitize_filename(dirty)
        self.assertNotIn("..", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn(":", clean)
        self.assertNotIn("*", clean)
        self.assertNotIn("?", clean)
        self.assertTrue(clean.endswith(".csv"))

    def test_validate_attachment_formats(self):
        # Valid CSV
        valid_csv, reason1 = self.service.validate_attachment("inventory.csv", 1024)
        self.assertTrue(valid_csv)
        self.assertIsNone(reason1)

        # Valid XLSX
        valid_xlsx, reason2 = self.service.validate_attachment("catalog.xlsx", 5000)
        self.assertTrue(valid_xlsx)
        self.assertIsNone(reason2)

        # Valid PDF format
        valid_pdf, reason3 = self.service.validate_attachment("invoice.pdf", 2048)
        self.assertTrue(valid_pdf)
        self.assertIsNone(reason3)

        # Invalid EXE format
        invalid_exe, reason_exe = self.service.validate_attachment("malicious.exe", 2048)
        self.assertFalse(invalid_exe)
        self.assertIn("UNSUPPORTED_FORMAT", reason_exe)

        # Empty attachment
        empty_att, reason4 = self.service.validate_attachment("empty.csv", 0)
        self.assertFalse(empty_att)
        self.assertIn("EMPTY_ATTACHMENT", reason4)

        # Over-sized attachment (> 25MB)
        huge_size = 30 * 1024 * 1024
        oversized, reason5 = self.service.validate_attachment("huge.csv", huge_size)
        self.assertFalse(oversized)
        self.assertIn("FILE_SIZE_EXCEEDED", reason5)

    def test_save_attachment_bytes(self):
        sample_bytes = b"Vendor,Product ID,Product Name,SKU,Quantity,Price,Updated Date\n"
        info = self.service.save_attachment_bytes(sample_bytes, "test_inventory.csv", vendor_prefix="VendorA")

        self.assertTrue(info.saved_path.exists())
        self.assertEqual(info.size_bytes, len(sample_bytes))
        self.assertTrue(info.is_valid_format)
        self.assertFalse(info.is_empty)
        self.assertEqual(len(info.file_hash), 64)  # 64 hex characters for SHA256


if __name__ == "__main__":
    unittest.main()
