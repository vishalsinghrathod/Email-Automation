"""
Unit Tests for Web Service Handler
Tests handler methods, routing, JSON formatting, and file ingestion logic
using mock I/O streams without requiring network socket permissions.
"""

import base64
import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config.settings import DATABASE_PATH
from services.database_service import DatabaseService
from services.web_service import AutomationRequestHandler, SPA_HTML_TEMPLATE


class DummyHandler(AutomationRequestHandler):
    """
    Subclass of AutomationRequestHandler that bypasses TCP socket initialization
    for fast, safe, and isolated unit testing.
    """
    def __init__(self, method: str, path: str, body: bytes = b""):
        self.command = method
        self.path = path
        self.requestline = f"{method} {path} HTTP/1.1"
        self.request_version = "HTTP/1.1"
        self.client_address = ("127.0.0.1", 8080)
        self.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.close_connection = True

        if method == "GET":
            self.do_GET()
        elif method == "POST":
            self.do_POST()
        elif method == "OPTIONS":
            self.do_OPTIONS()

    def get_response_body(self) -> bytes:
        raw = self.wfile.getvalue()
        # Header and body are separated by \r\n\r\n
        parts = raw.split(b"\r\n\r\n", 1)
        return parts[1] if len(parts) > 1 else b""

    def get_status_code(self) -> int:
        raw = self.wfile.getvalue()
        first_line = raw.split(b"\r\n", 1)[0].decode("utf-8")
        # e.g. "HTTP/1.0 200 OK"
        return int(first_line.split(" ")[1])


class TestWebServiceHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseService(DATABASE_PATH)
        cls.db.reset_demo_data()
        cls.db.seed_default_vendors()

    def test_root_serves_html(self):
        handler = DummyHandler("GET", "/")
        self.assertEqual(handler.get_status_code(), 200)
        body = handler.get_response_body().decode("utf-8")
        self.assertIn("Smart Vendor Email & Inventory Portal", body)
        self.assertIn("<!DOCTYPE html>", body)

    def test_api_status(self):
        handler = DummyHandler("GET", "/api/status")
        self.assertEqual(handler.get_status_code(), 200)
        data = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertIn("active_vendors", data)
        self.assertIn("inventory_distinct_products", data)
        self.assertIn("total_units_in_stock", data)
        self.assertIn("critical_alerts_count", data)

    def test_api_inventory(self):
        handler = DummyHandler("GET", "/api/inventory")
        self.assertEqual(handler.get_status_code(), 200)
        data = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertIsInstance(data, list)

    def test_api_rejected(self):
        handler = DummyHandler("GET", "/api/rejected")
        self.assertEqual(handler.get_status_code(), 200)
        data = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertIsInstance(data, list)

    def test_api_vendors(self):
        handler = DummyHandler("GET", "/api/vendors")
        self.assertEqual(handler.get_status_code(), 200)
        data = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_api_reset_demo(self):
        handler = DummyHandler("POST", "/api/reset-demo", b"{}")
        self.assertEqual(handler.get_status_code(), 200)
        data = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertTrue(data["success"])

    def test_api_upload_file(self):
        csv_text = "Vendor,Product ID,SKU,Product Name,Quantity,Price\nApex Global Supplies,APX-UI-01,APX-UI-99,Test Item UI,10,25.00\n"
        b64 = base64.b64encode(csv_text.encode("utf-8")).decode("utf-8")
        payload = json.dumps({"filename": "sample_ui_test.csv", "file_data": b64}).encode("utf-8")

        handler = DummyHandler("POST", "/api/upload", payload)
        self.assertEqual(handler.get_status_code(), 200)
        data = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertGreaterEqual(data["valid_count"], 1)

    def test_api_export_inventory(self):
        handler = DummyHandler("GET", "/api/export/inventory")
        self.assertEqual(handler.get_status_code(), 200)
        csv_content = handler.get_response_body().decode("utf-8")
        self.assertIn("Vendor,SKU,Product Name", csv_content)

    def test_api_monitored_accounts_flow(self):
        # 1. GET accounts
        h_get = DummyHandler("GET", "/api/monitored-accounts")
        self.assertEqual(h_get.get_status_code(), 200)
        accounts = json.loads(h_get.get_response_body().decode("utf-8"))
        self.assertIsInstance(accounts, list)

        # 2. POST link new account
        payload = json.dumps({
            "account_name": "Test Linked Vendor",
            "email_address": "test.vendor.flow@example.com",
            "provider_type": "GMAIL",
            "desktop_alerts": True,
        }).encode("utf-8")
        h_post = DummyHandler("POST", "/api/monitored-accounts", payload)
        self.assertEqual(h_post.get_status_code(), 200)
        res_post = json.loads(h_post.get_response_body().decode("utf-8"))
        self.assertTrue(res_post["success"])
        acc_id = res_post["id"]

        # 3. Toggle account
        t_payload = json.dumps({"id": acc_id, "is_active": False}).encode("utf-8")
        h_toggle = DummyHandler("POST", "/api/monitored-accounts/toggle", t_payload)
        self.assertEqual(h_toggle.get_status_code(), 200)
        res_toggle = json.loads(h_toggle.get_response_body().decode("utf-8"))
        self.assertTrue(res_toggle["success"])

        # 4. Delete/Unlink account
        d_payload = json.dumps({"id": acc_id}).encode("utf-8")
        h_del = DummyHandler("POST", "/api/monitored-accounts/delete", d_payload)
        self.assertEqual(h_del.get_status_code(), 200)
        res_del = json.loads(h_del.get_response_body().decode("utf-8"))
        self.assertTrue(res_del["success"])

    def test_api_emails_feed(self):
        handler = DummyHandler("GET", "/api/emails")
        self.assertEqual(handler.get_status_code(), 200)
        emails = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertIsInstance(emails, list)

    def test_api_desktop_alert_trigger(self):
        handler = DummyHandler("POST", "/api/test-desktop-alert", b"{}")
        self.assertEqual(handler.get_status_code(), 200)
        data = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertIn("success", data)

    def test_api_delete_email(self):
        from datetime import datetime
        from config.settings import DATABASE_PATH
        from services.database_service import DatabaseService
        db = DatabaseService(DATABASE_PATH)
        email_id = db.record_incoming_email(
            message_id="msg_web_del_456",
            sender_email="test@del.com",
            subject="Delete Test",
            received_at=datetime.now(),
            attachment_count=1,
            status="SUCCESS",
        )

        payload = json.dumps({"id": email_id, "delete_from_gmail": False}).encode("utf-8")
        handler = DummyHandler("POST", "/api/emails/delete", payload)
        self.assertEqual(handler.get_status_code(), 200)
        res = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertTrue(res["success"])

    def test_api_reset_demo_clear_all(self):
        payload = json.dumps({"clear_all": True}).encode("utf-8")
        handler = DummyHandler("POST", "/api/reset-demo", payload)
        self.assertEqual(handler.get_status_code(), 200)
        res = json.loads(handler.get_response_body().decode("utf-8"))
        self.assertTrue(res["success"])


if __name__ == "__main__":
    unittest.main()
