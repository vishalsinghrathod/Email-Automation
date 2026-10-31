-- Smart Vendor Email & Inventory Automation System
-- Database Schema for SQLite

PRAGMA foreign_keys = ON;

-- 1. Vendors Table
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    vendor_code TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Emails Table (tracks all inspected & processed emails)
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    thread_id TEXT,
    sender_name TEXT,
    sender_email TEXT NOT NULL,
    subject TEXT,
    body_preview TEXT,
    received_at TIMESTAMP,
    priority TEXT DEFAULT 'Normal', -- 'High Priority', 'Normal', 'Promotional', 'Requires Review'
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'SKIPPED'
    attachment_count INTEGER DEFAULT 0,
    error_message TEXT,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Email Logs Table (for backward compatibility and detailed query logs)
CREATE TABLE IF NOT EXISTS email_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    sender_email TEXT NOT NULL,
    subject TEXT,
    received_at TIMESTAMP,
    attachment_count INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Processing Queue Table (Module H: SQLite-backed persistent queue)
CREATE TABLE IF NOT EXISTS processing_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    priority_level INTEGER DEFAULT 2, -- 1: High, 2: Normal, 3: Promotional/Review
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Attachments Table
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER,
    message_id TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    original_filename TEXT,
    file_size INTEGER NOT NULL,
    status TEXT DEFAULT 'DOWNLOADED', -- 'DOWNLOADED', 'PROCESSED', 'REJECTED'
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (email_id) REFERENCES emails (id) ON DELETE CASCADE
);

-- Backward compatibility table for file checksum tracking
CREATE TABLE IF NOT EXISTS processed_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Inventory Items Table (upserted by vendor_id + product_id)
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    sku TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    updated_date TEXT NOT NULL,
    invoice_no TEXT,
    source_file TEXT,
    last_imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (vendor_id) REFERENCES vendors (id) ON DELETE CASCADE,
    UNIQUE (vendor_id, product_id)
);

-- 7. Notifications Log Table (Module C)
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    notification_type TEXT NOT NULL, -- 'DESKTOP', 'EMAIL'
    recipient TEXT,
    subject TEXT,
    status TEXT NOT NULL, -- 'SENT', 'FAILED'
    error_message TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Rejected Records Audit Table
CREATE TABLE IF NOT EXISTS rejected_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_log_id INTEGER,
    source_file TEXT,
    vendor_name TEXT,
    product_id TEXT,
    raw_data TEXT,
    rejection_reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 9. Processing Runs Summary Table
CREATE TABLE IF NOT EXISTS processing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_seconds REAL,
    emails_inspected INTEGER DEFAULT 0,
    emails_processed INTEGER DEFAULT 0,
    files_downloaded INTEGER DEFAULT 0,
    records_valid INTEGER DEFAULT 0,
    records_rejected INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    summary_text TEXT
);

-- 10. System State & Sync Checkpoint Table (Module A: tracks Gmail historyId & watch renewal)
CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 11. Monitored Email Accounts & Inboxes (Supports Multi-Email Tracking via UI)
CREATE TABLE IF NOT EXISTS monitored_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_name TEXT NOT NULL,
    email_address TEXT NOT NULL UNIQUE COLLATE NOCASE,
    provider_type TEXT DEFAULT 'GMAIL', -- 'GMAIL', 'IMAP', 'VENDOR_FEED', 'SIMULATED'
    is_active INTEGER DEFAULT 1,
    desktop_alerts INTEGER DEFAULT 1,
    total_emails_detected INTEGER DEFAULT 0,
    important_emails_detected INTEGER DEFAULT 0,
    last_checked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_emails_priority ON emails(priority);
CREATE INDEX IF NOT EXISTS idx_queue_status_prio ON processing_queue(status, priority_level);
CREATE INDEX IF NOT EXISTS idx_inv_vendor_prod ON inventory_items(vendor_id, product_id);
CREATE INDEX IF NOT EXISTS idx_inv_sku ON inventory_items(sku);
CREATE INDEX IF NOT EXISTS idx_attachments_hash ON attachments(file_hash);
CREATE INDEX IF NOT EXISTS idx_notifications_msgid ON notifications(message_id);
CREATE INDEX IF NOT EXISTS idx_monitored_email ON monitored_accounts(email_address);
