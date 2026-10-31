"""
Unit Tests for EmailClassifier Module
Tests classification rules, priority levels, and edge cases.
"""

import unittest
from services.email_classifier import EmailClassifier, PriorityLevel


class TestEmailClassifier(unittest.TestCase):
    def setUp(self):
        self.classifier = EmailClassifier(
            registered_vendors={"vendor.apex@gmail.com", "sales@nexuslogistics.com"}
        )

    def test_urgent_inventory_classified_as_high_priority(self):
        res = self.classifier.classify(
            sender_email="vendor.apex@gmail.com",
            subject="URGENT: Stockout Alert for SKU-102",
            body_text="Immediate action required. Low stock on major hardware items.",
            has_attachments=True,
        )
        self.assertEqual(res.category, PriorityLevel.HIGH_PRIORITY)
        self.assertEqual(res.priority_rank, 1)
        self.assertTrue(res.is_high_priority)
        self.assertTrue(res.is_actionable)
        self.assertIn("urgent_keywords", str(res.matched_rules))

    def test_registered_vendor_stock_update_classified_as_high_priority(self):
        res = self.classifier.classify(
            sender_email="sales@nexuslogistics.com",
            subject="Weekly Inventory Update Catalog",
            body_text="Please find our updated stock catalog attached.",
            has_attachments=True,
        )
        self.assertEqual(res.category, PriorityLevel.HIGH_PRIORITY)
        self.assertEqual(res.priority_rank, 1)
        self.assertTrue(res.is_high_priority)

    def test_payment_confirmation_classified_as_normal(self):
        res = self.classifier.classify(
            sender_email="billing@services.com",
            subject="Payment Confirmation - Transaction #48921",
            body_text="Thank you for your payment. Your receipt is enclosed.",
            has_attachments=False,
        )
        self.assertEqual(res.category, PriorityLevel.NORMAL)
        self.assertEqual(res.priority_rank, 2)
        self.assertFalse(res.is_high_priority)
        self.assertTrue(res.is_actionable)

    def test_promotional_email_classified_as_promotional(self):
        res = self.classifier.classify(
            sender_email="marketing@dealsnetwork.com",
            subject="Exclusive 50% Discount Sale on Electronics!",
            body_text="Click here to claim your coupon or unsubscribe.",
            has_attachments=False,
        )
        self.assertEqual(res.category, PriorityLevel.PROMOTIONAL)
        self.assertEqual(res.priority_rank, 3)
        self.assertFalse(res.is_high_priority)
        self.assertFalse(res.is_actionable)

    def test_unknown_sender_classified_as_requires_review(self):
        res = self.classifier.classify(
            sender_email="stranger@randomdomain.xyz",
            subject="Meeting notes and follow up tomorrow",
            body_text="Can we discuss the schedule?",
            has_attachments=False,
        )
        self.assertEqual(res.category, PriorityLevel.REQUIRES_REVIEW)
        self.assertEqual(res.priority_rank, 3)
        self.assertFalse(res.is_high_priority)
        self.assertFalse(res.is_actionable)

    def test_configurable_custom_rules(self):
        custom_classifier = EmailClassifier(
            urgent_keywords=["mayday", "hotfix"],
            promotional_keywords=["clearance"],
            registered_vendors={"custom@vendor.org"},
        )
        # Custom urgent keyword
        res1 = custom_classifier.classify(
            sender_email="support@client.com",
            subject="Server Hotfix Applied",
        )
        self.assertEqual(res1.category, PriorityLevel.HIGH_PRIORITY)

        # Custom promo keyword
        res2 = custom_classifier.classify(
            sender_email="shop@store.com",
            subject="Annual Clearance Event",
        )
        self.assertEqual(res2.category, PriorityLevel.PROMOTIONAL)


if __name__ == "__main__":
    unittest.main()

