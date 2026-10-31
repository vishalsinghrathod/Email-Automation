"""
Email Classification and Priority Engine
Module B - Smart Vendor Email & Inventory Automation System
Classifies incoming emails into:
  1. High Priority
  2. Normal
  3. Promotional
  4. Requires Review
Uses external, configurable rules without modifying core engine logic.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from config.settings import (
    HIGH_PRIORITY_KEYWORDS,
    PROMOTIONAL_KEYWORDS,
    REGISTERED_VENDOR_EMAILS,
    URGENT_KEYWORDS,
)

logger = logging.getLogger("inventory_automation")


class PriorityLevel(str, Enum):
    """Canonical classification categories."""
    HIGH_PRIORITY = "High Priority"
    NORMAL = "Normal"
    PROMOTIONAL = "Promotional"
    REQUIRES_REVIEW = "Requires Review"


@dataclass
class ClassificationResult:
    """Encapsulates the decision and explanation of email classification."""
    category: PriorityLevel
    priority_rank: int  # 1 (Highest) -> 3 (Lowest, e.g., promotional)
    is_high_priority: bool
    is_actionable: bool
    matched_rules: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "priority_rank": self.priority_rank,
            "is_high_priority": self.is_high_priority,
            "is_actionable": self.is_actionable,
            "matched_rules": self.matched_rules,
            "reason": self.reason,
        }


class EmailClassifier:
    """
    Rule-based, configurable email classification engine.
    Evaluates sender identity, keywords, attachments, and urgency markers.
    """

    def __init__(
        self,
        urgent_keywords: Optional[List[str]] = None,
        high_priority_keywords: Optional[List[str]] = None,
        promotional_keywords: Optional[List[str]] = None,
        registered_vendors: Optional[Set[str]] = None,
        vendor_check_callback: Optional[Callable[[str], bool]] = None,
    ):
        self.urgent_keywords = urgent_keywords or URGENT_KEYWORDS
        self.high_priority_keywords = high_priority_keywords or HIGH_PRIORITY_KEYWORDS
        self.promotional_keywords = promotional_keywords or PROMOTIONAL_KEYWORDS
        self.registered_vendors = {
            v.strip().lower() for v in (registered_vendors or REGISTERED_VENDOR_EMAILS)
        }
        self.vendor_check_callback = vendor_check_callback

    def is_vendor_registered(self, sender_email: str) -> bool:
        """Checks if sender is a recognized, registered vendor."""
        clean_email = sender_email.strip().lower()
        if clean_email in self.registered_vendors:
            return True
        if self.vendor_check_callback:
            try:
                return bool(self.vendor_check_callback(clean_email))
            except Exception as exc:
                logger.warning("Vendor check callback failed for '%s': %s", clean_email, exc)
        return False

    def classify(
        self,
        sender_email: str,
        subject: str,
        body_text: str = "",
        has_attachments: bool = False,
        is_vendor: Optional[bool] = None,
    ) -> ClassificationResult:
        """
        Classifies an email based on configurable rules.
        Order of evaluation:
          1. Promotional / Spam Filter
          2. Urgent / High Priority Inventory Indicators
          3. Normal Business & Vendor Operations
          4. Fallback: Unknown / Requires Review
        """
        clean_sender = (sender_email or "").strip().lower()
        clean_subject = (subject or "").lower()
        clean_body = (body_text or "").lower()
        combined_text = f"{clean_subject} {clean_body}"

        matched_rules: List[str] = []

        # Determine vendor registration status
        vendor_status = is_vendor if is_vendor is not None else self.is_vendor_registered(clean_sender)

        # ----------------------------------------------------------------------
        # Rule 1: Promotional / Marketing Check
        # ----------------------------------------------------------------------
        matched_promo = [k for k in self.promotional_keywords if re.search(r"\b" + re.escape(k) + r"\b", combined_text)]
        if matched_promo and not vendor_status:
            matched_rules.append(f"matched_promotional_keywords: {', '.join(matched_promo)}")
            return ClassificationResult(
                category=PriorityLevel.PROMOTIONAL,
                priority_rank=3,
                is_high_priority=False,
                is_actionable=False,
                matched_rules=matched_rules,
                reason=f"Identified as promotional content matching keywords: {', '.join(matched_promo)}",
            )

        # ----------------------------------------------------------------------
        # Rule 2: Urgent & Critical Inventory Updates (High Priority)
        # ----------------------------------------------------------------------
        matched_urgent = [k for k in self.urgent_keywords if re.search(r"\b" + re.escape(k) + r"\b", combined_text)]
        if matched_urgent:
            matched_rules.append(f"urgent_keywords: {', '.join(matched_urgent)}")
            if vendor_status:
                matched_rules.append("registered_vendor: yes")

            return ClassificationResult(
                category=PriorityLevel.HIGH_PRIORITY,
                priority_rank=1,
                is_high_priority=True,
                is_actionable=True,
                matched_rules=matched_rules,
                reason=f"High Priority: Contains urgent indicators ({', '.join(matched_urgent)})",
            )

        # ----------------------------------------------------------------------
        # Rule 3: Registered Vendor with Inventory / Invoices / Attachments (High Priority)
        # ----------------------------------------------------------------------
        matched_high_keywords = [
            k for k in self.high_priority_keywords if re.search(r"\b" + re.escape(k) + r"\b", combined_text)
        ]

        if vendor_status and (has_attachments or matched_high_keywords):
            if matched_high_keywords:
                matched_rules.append(f"inventory_keywords: {', '.join(matched_high_keywords)}")
            if has_attachments:
                matched_rules.append("attachment_present: yes")
            matched_rules.append("registered_vendor: yes")

            return ClassificationResult(
                category=PriorityLevel.HIGH_PRIORITY,
                priority_rank=1,
                is_high_priority=True,
                is_actionable=True,
                matched_rules=matched_rules,
                reason="High Priority: Registered vendor stock or invoice update",
            )

        # Unregistered sender with high-priority keyword and attachment
        if matched_high_keywords and has_attachments:
            matched_rules.append(f"inventory_keywords: {', '.join(matched_high_keywords)}")
            matched_rules.append("attachment_present: yes")
            return ClassificationResult(
                category=PriorityLevel.HIGH_PRIORITY,
                priority_rank=1,
                is_high_priority=True,
                is_actionable=True,
                matched_rules=matched_rules,
                reason="High Priority: Unregistered supplier with inventory attachment",
            )

        # ----------------------------------------------------------------------
        # Rule 4: Normal Business Operations (Normal Priority)
        # ----------------------------------------------------------------------
        normal_terms = ["payment confirmation", "purchase order", "po", "receipt", "delivery note", "shipping update"]
        matched_normal = [t for t in normal_terms if t in combined_text]

        if vendor_status:
            matched_rules.append("registered_vendor: yes")
            reason_text = "Normal: Routine correspondence from registered vendor"
            if matched_normal:
                matched_rules.append(f"normal_terms: {', '.join(matched_normal)}")
            return ClassificationResult(
                category=PriorityLevel.NORMAL,
                priority_rank=2,
                is_high_priority=False,
                is_actionable=True,
                matched_rules=matched_rules,
                reason=reason_text,
            )

        if matched_normal:
            matched_rules.append(f"normal_terms: {', '.join(matched_normal)}")
            return ClassificationResult(
                category=PriorityLevel.NORMAL,
                priority_rank=2,
                is_high_priority=False,
                is_actionable=True,
                matched_rules=matched_rules,
                reason=f"Normal: Standard business transaction ({', '.join(matched_normal)})",
            )

        # ----------------------------------------------------------------------
        # Rule 5: Unknown / Requires Review
        # ----------------------------------------------------------------------
        matched_rules.append("unregistered_vendor: unknown")
        return ClassificationResult(
            category=PriorityLevel.REQUIRES_REVIEW,
            priority_rank=3,
            is_high_priority=False,
            is_actionable=False,
            matched_rules=matched_rules,
            reason="Requires Review: Unrecognized sender and unclassified subject",
        )

