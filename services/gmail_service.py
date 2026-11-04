"""
Gmail Service Module
Handles secure Google OAuth 2.0 authentication, email queries,
attachment downloads via Gmail API, label updates, error retries,
and provides a separate Demo Inbox Provider for offline verification.
"""

import base64
import email.utils
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config.settings import (
    GMAIL_APPLY_LABEL,
    GMAIL_BATCH_SIZE,
    GMAIL_CREDENTIALS_PATH,
    GMAIL_MARK_AS_READ,
    GMAIL_SCOPES,
    GMAIL_SEARCH_KEYWORDS,
    GMAIL_TOKEN_PATH,
    REGISTERED_VENDOR_EMAILS,
)

logger = logging.getLogger("inventory_automation")


@dataclass
class EmailMessage:
    """Standardized internal representation of an inventory email."""
    message_id: str
    thread_id: str
    sender_name: str
    sender_email: str
    subject: str
    received_at: Optional[datetime]
    raw_parts: List[Dict[str, Any]] = field(default_factory=list)
    raw_payload: Optional[Dict[str, Any]] = None
    body_snippet: Optional[str] = None


class GmailService:
    """
    Production-ready Gmail API integration using Google OAuth 2.0.
    """

    def __init__(
        self,
        credentials_path: Path = GMAIL_CREDENTIALS_PATH,
        token_path: Path = GMAIL_TOKEN_PATH,
        scopes: Optional[List[str]] = None,
    ):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.scopes = scopes or GMAIL_SCOPES
        self.service = None
        self._custom_label_id: Optional[str] = None

    def authenticate(self) -> bool:
        """
        Authenticates with Google OAuth 2.0.
        Loads cached token.json, refreshes if expired, or runs user authorization flow.
        """
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            logger.error("Missing Google API libraries: %s. Install requirements.txt first.", exc)
            return False

        creds = None

        # Cloud/Render environment variable support for headless OAuth
        env_token = os.getenv("GMAIL_TOKEN_JSON")
        if env_token and not self.token_path.exists():
            try:
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                self.token_path.write_text(env_token.strip(), encoding="utf-8")
                logger.info("Restored token.json from GMAIL_TOKEN_JSON environment variable.")
            except Exception as exc:
                logger.warning("Could not write GMAIL_TOKEN_JSON to file: %s", exc)

        env_creds = os.getenv("GMAIL_CREDENTIALS_JSON")
        if env_creds and not self.credentials_path.exists():
            try:
                self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
                self.credentials_path.write_text(env_creds.strip(), encoding="utf-8")
                logger.info("Restored credentials.json from GMAIL_CREDENTIALS_JSON environment variable.")
            except Exception as exc:
                logger.warning("Could not write GMAIL_CREDENTIALS_JSON to file: %s", exc)

        # Check existing token
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), self.scopes)
                logger.debug("Loaded existing OAuth 2.0 credentials from '%s'", self.token_path)
            except Exception as exc:
                logger.warning("Existing token.json is invalid or corrupted (%s). Re-authenticating...", exc)
                creds = None

        # If credentials don't exist or are invalid
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("OAuth token expired. Refreshing access token...")
                    creds.refresh(Request())
                    logger.info("OAuth token refreshed successfully.")
                except Exception as exc:
                    logger.warning("Failed to refresh token: %s. Initiating new OAuth flow...", exc)
                    creds = None

            if not creds:
                if not self.credentials_path.exists():
                    logger.error(
                        "OAuth credentials file not found at '%s'. "
                        "Please download client secrets from Google Cloud Console.",
                        self.credentials_path,
                    )
                    return False

                logger.info("Initiating interactive OAuth 2.0 authorization flow...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path),
                    self.scopes,
                )
                creds = flow.run_local_server(port=0)

            # Persist token for future runs
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
            logger.info("Saved valid OAuth credentials to '%s'", self.token_path)

        self.service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail API service initialized successfully.")
        return True

    def build_search_query(
        self,
        vendor_emails: Optional[Set[str]] = None,
        keywords: Optional[List[str]] = None,
        unread_only: bool = True,
    ) -> str:
        """
        Builds an optimized Gmail search query.
        Example: is:unread has:attachment (from:a@x.com OR from:b@x.com) (inventory OR stock)
        """
        query_parts = []

        if unread_only:
            query_parts.append("is:unread")

        query_parts.append("has:attachment")

        active_vendors = vendor_emails or REGISTERED_VENDOR_EMAILS
        active_keywords = keywords or GMAIL_SEARCH_KEYWORDS

        if active_vendors and active_keywords:
            from_clause = " OR ".join([f"from:{email}" for email in active_vendors])
            keyword_clause = " OR ".join([f'"{kw}"' if " " in kw else kw for kw in active_keywords])
            query_parts.append(f"(({from_clause}) OR ({keyword_clause}))")
        elif active_vendors:
            from_clause = " OR ".join([f"from:{email}" for email in active_vendors])
            query_parts.append(f"({from_clause})")
        elif active_keywords:
            keyword_clause = " OR ".join([f'"{kw}"' if " " in kw else kw for kw in active_keywords])
            query_parts.append(f"({keyword_clause})")

        query = " ".join(query_parts)
        logger.debug("Constructed Gmail search query: %s", query)
        return query

    def fetch_unread_inventory_emails(
        self,
        vendor_emails: Optional[Set[str]] = None,
        keywords: Optional[List[str]] = None,
        max_results: int = GMAIL_BATCH_SIZE,
    ) -> List[EmailMessage]:
        """
        Fetches unread inventory emails matching vendor whitelist and subject keywords.
        """
        if not self.service:
            if not self.authenticate():
                logger.error("Unable to fetch emails: Gmail service not authenticated.")
                return []

        query = self.build_search_query(vendor_emails=vendor_emails, keywords=keywords)
        results: List[EmailMessage] = []

        try:
            # Query message list
            response = self._execute_with_retry(
                lambda: self.service.users().messages().list(
                    userId="me",
                    q=query,
                    maxResults=max_results,
                ).execute()
            )

            messages = response.get("messages", [])
            logger.info("Found %d matching emails in Gmail inbox.", len(messages))

            for msg_summary in messages:
                msg_id = msg_summary["id"]
                try:
                    full_msg = self._execute_with_retry(
                        lambda: self.service.users().messages().get(
                            userId="me",
                            id=msg_id,
                            format="full",
                        ).execute()
                    )
                    parsed_email = self._parse_message(full_msg)
                    results.append(parsed_email)
                except Exception as exc:
                    logger.error("Failed to retrieve or parse email %s: %s", msg_id, exc)

        except Exception as exc:
            logger.error("Error executing Gmail messages list: %s", exc)

        return results

    def _parse_message(self, raw_message: Dict[str, Any]) -> EmailMessage:
        """
        Parses Gmail API message dictionary into EmailMessage dataclass.
        """
        msg_id = raw_message["id"]
        thread_id = raw_message.get("threadId", "")
        payload = raw_message.get("payload", {})
        headers = payload.get("headers", [])

        # Extract headers
        header_map = {h["name"].lower(): h["value"] for h in headers}
        raw_from = header_map.get("from", "")
        sender_name, sender_email = email.utils.parseaddr(raw_from)
        sender_email = sender_email.lower().strip()
        sender_name = sender_name.strip() or sender_email

        subject = header_map.get("subject", "(No Subject)")

        # Parse date
        received_at = None
        raw_date = header_map.get("date")
        if raw_date:
            try:
                parsed_tuple = email.utils.parsedate_to_datetime(raw_date)
                received_at = parsed_tuple
            except Exception:
                received_at = datetime.now()

        # Collect parts recursively (handles nested multipart MIME)
        raw_parts = self._flatten_parts(payload)

        # Extract message body text from parts or snippet
        body_snippet = raw_message.get("snippet", "")
        for p in raw_parts:
            if p.get("mimeType") == "text/plain" and not p.get("filename"):
                b64_data = p.get("body", {}).get("data", "")
                if b64_data:
                    try:
                        decoded = base64.urlsafe_b64decode(b64_data).decode("utf-8", errors="ignore").strip()
                        if decoded:
                            body_snippet = decoded
                            break
                    except Exception:
                        pass

        if not body_snippet:
            for p in raw_parts:
                if p.get("mimeType") == "text/html" and not p.get("filename"):
                    b64_data = p.get("body", {}).get("data", "")
                    if b64_data:
                        try:
                            decoded = base64.urlsafe_b64decode(b64_data).decode("utf-8", errors="ignore").strip()
                            clean_text = re.sub(r"<[^>]+>", " ", decoded)
                            clean_text = re.sub(r"\s+", " ", clean_text).strip()
                            if clean_text:
                                body_snippet = clean_text
                                break
                        except Exception:
                            pass

        return EmailMessage(
            message_id=msg_id,
            thread_id=thread_id,
            sender_name=sender_name,
            sender_email=sender_email,
            subject=subject,
            received_at=received_at,
            raw_parts=raw_parts,
            raw_payload=payload,
            body_snippet=body_snippet,
        )

    def _flatten_parts(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Recursively extracts all parts from a nested multipart email.
        """
        parts = []
        if "parts" in payload:
            for part in payload["parts"]:
                parts.extend(self._flatten_parts(part))
        else:
            parts.append(payload)
        return parts

    def _get_or_create_label_id(self, label_name: str) -> Optional[str]:
        """
        Finds or creates a Gmail user label for tracking processed emails.
        """
        if self._custom_label_id:
            return self._custom_label_id

        try:
            labels_response = self.service.users().labels().list(userId="me").execute()
            labels = labels_response.get("labels", [])
            for label in labels:
                if label["name"].lower() == label_name.lower():
                    self._custom_label_id = label["id"]
                    return self._custom_label_id

            # Create label if not found
            new_label = {
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            }
            created = self.service.users().labels().create(userId="me", body=new_label).execute()
            self._custom_label_id = created["id"]
            logger.info("Created Gmail label '%s' (ID: %s)", label_name, self._custom_label_id)
            return self._custom_label_id
        except Exception as exc:
            logger.warning("Could not create or fetch label '%s': %s", label_name, exc)
            return None

    def mark_email_as_processed(
        self,
        message_id: str,
        mark_as_read: bool = GMAIL_MARK_AS_READ,
        apply_label: Optional[str] = GMAIL_APPLY_LABEL,
    ) -> bool:
        """
        Updates email state in Gmail: removes 'UNREAD' and applies processed label.
        """
        if not self.service:
            return False

        remove_labels = ["UNREAD"] if mark_as_read else []
        add_labels = []

        if apply_label:
            label_id = self._get_or_create_label_id(apply_label)
            if label_id:
                add_labels.append(label_id)

        if not remove_labels and not add_labels:
            return True

        body = {
            "removeLabelIds": remove_labels,
            "addLabelIds": add_labels,
        }

        try:
            self._execute_with_retry(
                lambda: self.service.users().messages().modify(
                    userId="me",
                    id=message_id,
                    body=body,
                ).execute()
            )
            logger.info("Marked message '%s' as processed (Removed: %s, Added: %s)", message_id, remove_labels, add_labels)
            return True
        except Exception as exc:
            logger.error("Failed to update status for message '%s': %s", message_id, exc)
            return False

    def trash_message(self, message_id: str) -> bool:
        """
        Moves an email to Gmail Trash (safe delete).
        """
        if not self.service:
            if not self.authenticate():
                return False
        try:
            self._execute_with_retry(
                lambda: self.service.users().messages().trash(userId="me", id=message_id).execute()
            )
            logger.info("Moved email '%s' to Gmail Trash.", message_id)
            return True
        except Exception as exc:
            logger.warning("Failed to trash Gmail message '%s': %s", message_id, exc)
            return False

    @staticmethod
    def _execute_with_retry(api_call, max_retries: int = 3, initial_delay: float = 1.5):
        """
        Executes a Gmail API call with exponential backoff for rate limits and transient errors.
        """
        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            try:
                return api_call()
            except Exception as exc:
                # Check for rate limit or server error
                status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
                if status_code in (429, 500, 503) and attempt < max_retries:
                    logger.warning("Gmail API error (%s). Retrying in %.1fs (attempt %d/%d)...", exc, delay, attempt, max_retries)
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise


# ==============================================================================
# Demo Inbox Provider (Offline / Simulation Mode)
# ==============================================================================

class DemoInboxProvider:
    """
    Simulates a Gmail inbox using local test files and vendor records.
    Allows complete testing of the download, processing, validation, database,
    and reporting pipeline without requiring real Google Cloud credentials.
    """

    def __init__(self, sample_dir: Optional[Path] = None):
        self.sample_dir = sample_dir or (Path(__file__).resolve().parent.parent / "tests" / "data")
        self.processed_ids: Set[str] = set()

    def fetch_unread_inventory_emails(
        self,
        vendor_emails: Optional[Set[str]] = None,
        keywords: Optional[List[str]] = None,
        max_results: int = 10,
    ) -> List[EmailMessage]:
        """
        Simulates fetching unread vendor inventory emails from disk.
        """
        if not self.sample_dir.exists():
            logger.warning("Sample directory '%s' does not exist.", self.sample_dir)
            return []

        demo_emails: List[EmailMessage] = []

        # Find sample data files (CSV, XLSX, PDF)
        sample_files = sorted(
            list(self.sample_dir.glob("*.csv"))
            + list(self.sample_dir.glob("*.xlsx"))
            + list(self.sample_dir.glob("*.pdf"))
        )

        vendor_profiles = [
            ("Apex Global Supplies", "vendor.apex@gmail.com", "Apex Weekly Stock Inventory Update"),
            ("Nexus Logistics", "sales@nexuslogistics.com", "Nexus Logistics Inventory Catalog Feed"),
            ("Prime Hardware Corp", "orders@primehardware.com", "Prime Hardware Daily Inventory - New Stock"),
        ]

        for idx, file_path in enumerate(sample_files):
            msg_id = f"demo_msg_{idx + 1}_{file_path.stem}"
            if msg_id in self.processed_ids:
                continue

            v_name, v_email, subject_prefix = vendor_profiles[idx % len(vendor_profiles)]
            subject = f"{subject_prefix} [{file_path.name}]"

            # Read file bytes for simulated inline attachment
            with open(file_path, "rb") as f:
                content = f.read()

            b64_data = base64.urlsafe_b64encode(content).decode("utf-8")

            part = {
                "filename": file_path.name,
                "body": {
                    "data": b64_data,
                    "size": len(content),
                },
            }

            demo_emails.append(
                EmailMessage(
                    message_id=msg_id,
                    thread_id=f"demo_thread_{idx + 1}",
                    sender_name=v_name,
                    sender_email=v_email,
                    subject=subject,
                    received_at=datetime.now(),
                    raw_parts=[part],
                )
            )

        logger.info("[DEMO MODE] Loaded %d simulated vendor emails with attachments.", len(demo_emails))
        return demo_emails

    def mark_email_as_processed(self, message_id: str, **kwargs) -> bool:
        """Simulates marking an email as read/processed."""
        self.processed_ids.add(message_id)
        logger.info("[DEMO MODE] Marked simulated email '%s' as processed.", message_id)
        return True

    def trash_message(self, message_id: str) -> bool:
        """Simulates trashing an email in demo mode."""
        self.processed_ids.add(message_id)
        logger.info("[DEMO MODE] Simulated email '%s' moved to trash.", message_id)
        return True
