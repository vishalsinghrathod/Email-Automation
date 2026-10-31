# 📧 Vendor Inventory & Invoice Email Automation System

> **An enterprise-grade, resilient Python pipeline that automates email ingestion, multi-format attachment extraction (CSV, Excel, PDF), data normalization, fraud/anomaly detection, and executive alert monitoring.**

---

## 🎯 The Real-World Business Problem
In retail, e-commerce, distribution, and manufacturing companies, operations teams receive **dozens of emails daily** from multiple vendors containing inventory stock feeds, price updates, and invoices in disparate formats (**Excel, CSV, PDF**).

### The Pain Points:
1. **Manual Labor:** Teams spend 2–3 hours every day manually opening attachments and entering data.
2. **Stockouts & Delayed Restocking:** Low stock alerts are discovered too late, leading to lost customer sales.
3. **Price Discrepancies:** Unannounced vendor price surges or corrupted negative prices slip through undetected.
4. **Human Errors & Duplicates:** Reprocessing the same invoice leads to double-counted stock in the ERP database.

---

## 💡 The Solution
This project automates the entire ingestion lifecycle end-to-end:
```
                                   ┌─────────────────────────┐
                                   │   Incoming Vendor Email  │
                                   └────────────┬────────────┘
                                                │
                          ┌─────────────────────▼─────────────────────┐
                          │         Gmail API / OAuth 2.0             │
                          │   (or Zero-Config Offline Demo Engine)   │
                          └─────────────────────┬─────────────────────┘
                                                │
                         ┌──────────────────────▼──────────────────────┐
                         │ Attachment Inspection & SHA-256 Deduplication│
                         │    (.csv, .xlsx, .xls, .pdf, .txt, .tsv)     │
                         └──────────────────────┬──────────────────────┘
                                                │
                         ┌──────────────────────▼──────────────────────┐
                         │   Data Normalization & Business Validation  │
                         │   • Mandatory check: SKU, Product, Qty, Cost│
                         │   • Rejects: Negative qty/price, duplicates │
                         └──────────────┬──────────────────────┬───────┘
                                        │                      │
                   [Valid Records]      │                      │   [Rejected Records]
                                        ▼                      ▼
                    ┌───────────────────────┐      ┌─────────────────────────┐
                    │ SQLite Inventory DB   │      │ Audit & Compliance Log  │
                    │ (Idempotent Upsert)   │      │ (Vendor Error Feedback) │
                    └───────────┬───────────┘      └────────────┬────────────┘
                                │                               │
                                └───────────────┬───────────────┘
                                                │
                          ┌─────────────────────▼─────────────────────┐
                          │   Live Alert Engine & Anomaly Highlights  │
                          │   🔴 Critical: Zero & Low Stock (≤ 10)    │
                          │   🟡 Warning: High Unit Price / Spikes    │
                          │   📊 Interactive HTML Monitoring Dashboard│
                          └───────────────────────────────────────────┘
```

---

## 🚀 Key Features

### 1. Multi-Format Attachment Support
- **Excel Sheets (`.xlsx`, `.xls`):** Parses vendor product catalogs and stock files.
- **CSV / Delimited Files (`.csv`, `.tsv`, `.txt`):** Automatic delimiter sniffing and encoding fallback (`utf-8`, `latin1`, `cp1252`).
- **PDF Invoices & Catalogs (`.pdf`):** Extracts tabular line items (`SKU`, `Product`, `Qty`, `Price`, and `Invoice #`) using `pypdf`.

### 2. Live Alerts & Highlighting Engine
- 🚨 **Critical Shortages:** Immediately flags items with `0 units` (Out of Stock) or `≤ 10 units` (Low Stock).
- ⚠️ **Price Anomaly Detection:** Flags items where price exceeds warning limits (e.g. `≥ $500.00`).
- 🛑 **Data Quality Auditing:** Corrupt rows (missing product IDs, negative numbers, bad dates) are segregated into audit logs without stopping the pipeline.

### 3. Dual UI Modes: Non-Technical Employees & Developers
- **Non-Technical Employee Web Portal (`start_app.bat` or `python main.py --ui`):** A modern local web interface running at `http://127.0.0.1:8080`. Employees can trigger inbox checks with a button, drag-and-drop vendor files (CSV/Excel/PDF), monitor stock shortage alerts, and export cleaned CSVs without touching the terminal.
- **Developer Command Line Interface (CLI):** Full control for engineers via `python main.py --demo`, `--view-inventory`, `--status`, `--view-alerts`, and `python scheduler.py`.

### 4. Smart Email Classification & Persistent Queueing
- **4 Priority Levels:** Classifies into `High Priority`, `Normal`, `Promotional`, and `Requires Review` using configurable rules.
- **SQLite-Backed Priority Queue:** High-priority inventory emails are dispatched first.
- **Crash Recovery:** If the server or app restarts mid-cycle, interrupted `PROCESSING` jobs are automatically restored to `PENDING`.

### 5. Dual Notification System (Module C)
- **Desktop Toasts (Plyer):** Instant Windows notifications for high-priority vendor emails.
- **Email Alerts:** Automatically drafts and dispatches alert emails via Gmail API.
- **Fault-Isolated:** A failure in notifications never impedes inventory data extraction.

### 6. Enterprise-Grade Reliability
- **Idempotency:** Email message IDs and SHA-256 file hashes prevent duplicate data imports even if run repeatedly.
- **Atomic Upserts:** SQLite `ON CONFLICT(vendor_id, product_id) DO UPDATE` ensures existing stock is refreshed accurately.
- **Database Schema:** Clean separation of concerns with `database/schema.sql` and `database/db.py`.

---

## 🖥️ How Non-Technical Employees Use It (Zero-Code Web UI)

Employees who do not know coding can simply double-click the desktop launcher:
1. Double-click **`start_app.bat`** (or run `python main.py --ui`).
2. Their browser immediately opens the **Smart Vendor Portal** (`http://127.0.0.1:8080`).
3. From the web UI, employees can:
   - Click **"🚀 Check Real Inbox"** or **"🧪 Run Demo Run"** to trigger automation with 1 click.
   - Drag-and-drop supplier files (CSV, Excel, PDF) onto the page to ingest them instantly.
   - Filter out-of-stock and low-stock items with visual colored alerts.
   - Click **"📥 Export CSV"** to download cleaned data.
   - Click **"🔄 Reset Demo"** to refresh sample data.

---

## ⚡ Developer & Interviewer Quick Demo (CLI Mode)

You can demonstrate this complete pipeline **offline in 5 seconds** without needing internet or real Gmail credentials:

### 1. Run Complete Demo Pipeline
```powershell
python main.py --demo
```
*Processes simulated CSV, Excel, and PDF invoice emails, flags anomalies, and generates reports.*

### 2. View Color-Coded Stock Table with Alert Status
```powershell
python main.py --view-inventory
```

### 3. View Active Critical Alerts Only
```powershell
python main.py --view-alerts
```

### 4. Launch the Interactive HTML Dashboard in Browser
```powershell
python main.py --dashboard
```

### 5. Reset Demo (To re-run anytime in front of interviewers)
```powershell
python main.py --reset-demo
```

---

## 🔌 Connecting Real Gmail (Production Mode)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project and enable the **Gmail API**.
3. Create **OAuth 2.0 Client ID** (Application type: *Desktop App*).
4. Download the JSON file and save it in this project folder as `credentials.json`.
5. Run:
```powershell
python main.py
```
*Your browser will open once to authorize access. Future runs use the cached `token.json` automatically.*

---

## 🧪 Automated Testing
Run the comprehensive test suite verifying database idempotency, format validation, PDF parsing, and end-to-end integration:

```powershell
pytest -v
```

---

## 🎤 Interview Talking Points

| Question Interviewers Ask | How This Project Solves It |
|---|---|
| *"How do you prevent duplicate email processing?"* | Message IDs are tracked in `email_logs`, and SHA-256 file hashes are stored in `processed_files` before parsing. |
| *"What happens if a vendor sends a corrupted file?"* | The file format and schema validators segregate corrupt rows to `rejected_records` with exact error reasons without crashing the batch. |
| *"How do you handle different column names from vendors?"* | A fuzzy column alias dictionary maps `Item No`, `Product Code`, `PID`, `Part #` to a canonical schema (`product_id`, `sku`, etc.). |
| *"How do you extract data from PDF invoices?"* | `pypdf` streams extract text, locate header anchors, and parse multi-column invoice line items and invoice numbers. |

