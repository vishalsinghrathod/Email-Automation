"""
Employee Web Portal Service
Provides a lightweight, zero-dependency, local web application using Python's
built-in http.server so non-technical employees can run pipelines, link and monitor
multiple email accounts, receive Windows desktop alerts, and view stock anomalies.
"""

import base64
import csv
import io
import json
import logging
import re
import socketserver
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import (
    DATABASE_PATH,
    DOWNLOADS_DIR,
    LOW_STOCK_ALERT_THRESHOLD,
    PRICE_SPIKE_ALERT_THRESHOLD,
    PROCESSED_DIR,
)
from services.attachment_service import AttachmentService
from services.database_service import DatabaseService
from services.inventory_processor import InventoryProcessor
from services.notification_service import NotificationService

logger = logging.getLogger("vendor_automation.web")


# ==============================================================================
# Global Background Poller State
# ==============================================================================
class AutoMonitorState:
    is_running = True
    poll_interval_seconds = 45
    last_run_time: Optional[datetime] = None
    last_status: str = "IDLE"
    last_summary: str = "Ready"

auto_monitor_state = AutoMonitorState()


# ==============================================================================
# HTML, CSS & JavaScript UI Template (Single Page Application)
# ==============================================================================

SPA_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
    <title>Smart Vendor Email & Inventory Portal</title>
    <style>
        :root {
            --bg: #090d16;
            --surface: #131c2e;
            --surface-hover: #1e293b;
            --border: #22314e;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
            --critical: #ef4444;
            --critical-bg: rgba(239, 68, 68, 0.12);
            --warning: #f59e0b;
            --warning-bg: rgba(245, 158, 11, 0.12);
            --success: #10b981;
            --success-bg: rgba(16, 185, 129, 0.12);
            --purple: #8b5cf6;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text-primary);
            line-height: 1.5;
            padding: 18px 24px 60px 24px;
            max-width: 100vw;
            overflow-x: hidden;
        }

        /* Top Bar */
        .navbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            padding-bottom: 18px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 20px;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .brand-icon {
            font-size: 26px;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            width: 46px;
            height: 46px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
        }
        .brand-text h1 {
            font-size: 21px;
            font-weight: 700;
            letter-spacing: -0.02em;
        }
        .brand-text p {
            font-size: 12px;
            color: var(--text-secondary);
        }
        .nav-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }

        /* Auto-Monitor Status Indicator */
        .monitor-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid var(--success);
            color: var(--success);
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }
        .pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
            box-shadow: 0 0 8px var(--success);
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% { opacity: 0.4; transform: scale(0.9); }
            50% { opacity: 1; transform: scale(1.15); }
            100% { opacity: 0.4; transform: scale(0.9); }
        }

        /* Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            border: 1px solid transparent;
            transition: all 0.2s;
            text-decoration: none;
            white-space: nowrap;
        }
        .btn-primary { background: var(--accent); color: #fff; }
        .btn-primary:hover { background: var(--accent-hover); }
        .btn-demo { background: var(--purple); color: #fff; }
        .btn-demo:hover { background: #7c3aed; }
        .btn-outline {
            background: var(--surface);
            color: var(--text-primary);
            border-color: var(--border);
        }
        .btn-outline:hover { background: var(--surface-hover); border-color: #475569; }
        .btn-danger {
            background: rgba(239, 68, 68, 0.15);
            color: var(--critical);
            border-color: rgba(239, 68, 68, 0.4);
        }
        .btn-danger:hover { background: var(--critical); color: #fff; }
        .btn-success {
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            border-color: var(--success);
        }
        .btn-success:hover { background: var(--success); color: #000; }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }

        /* Stat Cards Ribbon */
        .stats-ribbon {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 14px 16px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            cursor: pointer;
            transition: transform 0.2s, border-color 0.2s;
        }
        .stat-card:hover {
            transform: translateY(-2px);
            border-color: #3b82f6;
        }
        .stat-card.critical {
            border-color: var(--critical);
            background: linear-gradient(135deg, var(--surface) 60%, rgba(239, 68, 68, 0.15) 100%);
        }
        .stat-card.warning {
            border-color: var(--warning);
            background: linear-gradient(135deg, var(--surface) 60%, rgba(245, 158, 11, 0.15) 100%);
        }
        .stat-card.highlight {
            border-color: var(--purple);
            background: linear-gradient(135deg, var(--surface) 60%, rgba(139, 92, 246, 0.15) 100%);
        }
        .stat-label {
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 700;
            color: var(--text-secondary);
            letter-spacing: 0.05em;
        }
        .stat-value {
            font-size: 24px;
            font-weight: 800;
            line-height: 1.1;
        }
        .stat-desc {
            font-size: 11px;
            color: var(--text-secondary);
        }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 6px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 18px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }
        .tab-btn {
            background: transparent;
            color: var(--text-secondary);
            border: none;
            border-bottom: 2px solid transparent;
            padding: 11px 16px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.2s;
        }
        .tab-btn:hover { color: var(--text-primary); }
        .tab-btn.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
        }

        /* Tab Content Panes */
        .tab-pane { display: none; }
        .tab-pane.active { display: block; }

        /* Toolbar */
        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
            background: var(--surface);
            padding: 10px 14px;
            border-radius: 10px;
            border: 1px solid var(--border);
            margin-bottom: 14px;
        }
        .search-box {
            flex: 1;
            min-width: 240px;
        }
        .search-box input {
            width: 100%;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text-primary);
            font-size: 13px;
            outline: none;
        }
        .search-box input:focus { border-color: var(--accent); }
        .filters {
            display: flex;
            gap: 6px;
            overflow-x: auto;
            flex-wrap: wrap;
        }
        .filter-btn {
            background: var(--surface-hover);
            color: var(--text-secondary);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
        }
        .filter-btn.active, .filter-btn:hover {
            background: var(--accent);
            color: #fff;
            border-color: var(--accent);
        }

        /* Cards Grid for Monitored Accounts */
        .accounts-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
        }
        .account-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            position: relative;
            transition: all 0.2s;
        }
        .account-card.active {
            border-left: 4px solid var(--success);
        }
        .account-card.paused {
            border-left: 4px solid #64748b;
            opacity: 0.75;
        }
        .account-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 8px;
        }
        .account-name {
            font-size: 15px;
            font-weight: 700;
        }
        .account-email {
            font-family: monospace;
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 2px;
        }
        .account-stats {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            background: rgba(0, 0, 0, 0.25);
            padding: 10px;
            border-radius: 8px;
        }
        .account-stat-num {
            font-size: 18px;
            font-weight: 800;
        }
        .account-stat-lbl {
            font-size: 11px;
            color: var(--text-secondary);
        }
        .account-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
            color: var(--text-secondary);
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding-top: 10px;
        }
        .account-actions {
            display: flex;
            gap: 6px;
        }

        /* Email Rows with Highlights */
        tr.email-high-priority {
            background: rgba(239, 68, 68, 0.08);
            border-left: 3px solid var(--critical);
        }
        tr.email-high-priority:hover {
            background: rgba(239, 68, 68, 0.14);
        }

        /* Table & Cards */
        .table-responsive {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }
        th {
            background: #0f172a;
            padding: 10px 14px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }
        td {
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
            vertical-align: middle;
        }
        tr:hover td { background: rgba(255, 255, 255, 0.02); }
        tr.table-danger td { background: var(--critical-bg); }
        tr.table-warning td { background: var(--warning-bg); }

        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }
        .badge-critical { background: var(--critical); color: #fff; }
        .badge-warning { background: var(--warning); color: #000; }
        .badge-success { background: var(--success); color: #000; }
        .badge-normal { background: #334155; color: #f8fafc; }
        .badge-high { background: #dc2626; color: #fff; animation: pulse 2s infinite; }

        .tag-vendor {
            background: #1e293b;
            border: 1px solid var(--border);
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 12px;
        }
        .tag-invoice {
            font-family: monospace;
            background: #090d16;
            border: 1px solid var(--border);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 11px;
        }
        .source-name {
            font-family: monospace;
            font-size: 11px;
            color: var(--text-secondary);
            max-width: 170px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            display: inline-block;
        }

        /* Dropzone / Upload Area */
        .dropzone-container {
            background: var(--surface);
            border: 2px dashed #334155;
            border-radius: 14px;
            padding: 35px 20px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
            margin-bottom: 20px;
        }
        .dropzone-container:hover, .dropzone-container.drag-over {
            border-color: var(--accent);
            background: rgba(59, 130, 246, 0.05);
        }
        .dropzone-icon { font-size: 38px; margin-bottom: 10px; }
        .dropzone-text h3 { font-size: 15px; margin-bottom: 4px; }
        .dropzone-text p { color: var(--text-secondary); font-size: 12px; }
        .file-input { display: none; }

        /* Notification Toast */
        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #1e293b;
            color: #fff;
            border: 1px solid var(--border);
            border-left: 4px solid var(--accent);
            padding: 12px 18px;
            border-radius: 8px;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 12px;
            z-index: 9999;
            transform: translateY(100px);
            opacity: 0;
            transition: all 0.3s ease;
        }
        .toast.show { transform: translateY(0); opacity: 1; }
        .toast.success { border-left-color: var(--success); }
        .toast.error { border-left-color: var(--critical); }

        /* Modals */
        .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.7);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            padding: 16px;
        }
        .modal-overlay.active { display: flex; }
        .modal {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            max-width: 480px;
            width: 100%;
            padding: 22px;
            box-shadow: 0 20px 30px rgba(0, 0, 0, 0.6);
        }
        .modal h2 { font-size: 17px; margin-bottom: 12px; }
        .modal p { color: var(--text-secondary); font-size: 13px; margin-bottom: 16px; }
        .form-group {
            margin-bottom: 14px;
        }
        .form-group label {
            display: block;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            margin-bottom: 6px;
        }
        .form-group input, .form-group select {
            width: 100%;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text-primary);
            font-size: 13px;
            outline: none;
        }
        .form-group input:focus, .form-group select:focus {
            border-color: var(--accent);
        }
        .form-check {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 16px;
            font-size: 13px;
        }
        .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }

        /* Spinner */
        .spinner {
            display: inline-block;
            width: 13px;
            height: 13px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: #fff;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* Responsive Mobile Layout */
        @media (max-width: 768px) {
            body { padding: 12px; }
            .navbar { flex-direction: column; align-items: flex-start; }
            .nav-actions { width: 100%; justify-content: flex-start; }
            .stats-ribbon { grid-template-columns: repeat(2, 1fr); gap: 10px; }
            .stat-value { font-size: 20px; }
            .toolbar { flex-direction: column; align-items: stretch; }
            .search-box { width: 100%; }

            /* Table to Card conversion */
            .table-responsive table,
            .table-responsive thead,
            .table-responsive tbody,
            .table-responsive tr,
            .table-responsive td {
                display: block;
                width: 100%;
            }
            .table-responsive thead { display: none; }
            .table-responsive tr {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 10px;
                padding: 10px 12px;
                margin-bottom: 10px;
            }
            .table-responsive td {
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                padding: 5px 0;
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }
            .table-responsive td:last-child { border-bottom: none; }
            .table-responsive td::before {
                content: attr(data-label);
                font-weight: 600;
                font-size: 11px;
                color: var(--text-secondary);
                text-transform: uppercase;
                margin-right: 10px;
            }
            .table-responsive .source-name { max-width: 100%; white-space: normal; word-break: break-all; }
        }
    </style>
</head>
<body>

    <!-- Top Navigation Bar -->
    <header class="navbar">
        <div class="brand">
            <div class="brand-icon">📦</div>
            <div class="brand-text">
                <h1>Smart Vendor Email & Inventory Portal</h1>
                <p>Automated Multi-Email Monitoring, Desktop Alerts & Anomaly Highlights</p>
            </div>
        </div>
        <div class="nav-actions">
            <div class="monitor-pill" id="autoMonitorPill">
                <span class="pulse-dot"></span>
                <span id="monitorStatusText">Auto-Monitor: Active</span>
            </div>
            <button class="btn btn-outline" id="btnTestAlert" onclick="testDesktopAlert()">
                🔔 Test Desktop Alert
            </button>
            <button class="btn btn-demo" id="btnRunDemo" onclick="triggerPipeline(true)">
                🧪 Run Demo Scan
            </button>
            <button class="btn btn-primary" id="btnRunLive" onclick="triggerPipeline(false)">
                🚀 Scan Inboxes Now
            </button>
            <button class="btn btn-outline" onclick="openLinkModal()">
                ➕ Link Email
            </button>
            <button class="btn btn-danger" onclick="confirmResetDemo()">
                🔄 Reset Demo
            </button>
        </div>
    </header>

    <!-- Stats Ribbon -->
    <section class="stats-ribbon">
        <div class="stat-card" onclick="setTab('accounts')">
            <span class="stat-label">Monitored Accounts</span>
            <span class="stat-value" id="statActiveAccounts">-</span>
            <span class="stat-desc">Linked email inboxes</span>
        </div>
        <div class="stat-card highlight" onclick="setTab('emails')">
            <span class="stat-label" style="color: var(--purple);">Important Emails</span>
            <span class="stat-value" id="statImportantEmails" style="color: var(--purple);">-</span>
            <span class="stat-desc">Flagged with desktop alert</span>
        </div>
        <div class="stat-card critical" onclick="setTab('inventory'); filterInventory('critical');">
            <span class="stat-label" style="color: var(--critical);">Critical Shortages</span>
            <span class="stat-value" id="statCriticalAlerts" style="color: var(--critical);">-</span>
            <span class="stat-desc">Stock &le; 10 units (Out/Low)</span>
        </div>
        <div class="stat-card warning" onclick="setTab('inventory'); filterInventory('warning');">
            <span class="stat-label" style="color: var(--warning);">Price Warnings</span>
            <span class="stat-value" id="statWarningAlerts" style="color: var(--warning);">-</span>
            <span class="stat-desc">Unit price &ge; $500.00</span>
        </div>
        <div class="stat-card" onclick="setTab('inventory')">
            <span class="stat-label">Total In Stock</span>
            <span class="stat-value" id="statTotalUnits">-</span>
            <span class="stat-desc">Inventory units stored</span>
        </div>
    </section>

    <!-- Tabs Navigation -->
    <nav class="tabs">
        <button class="tab-btn active" onclick="setTab('accounts', this)">📧 Monitored Inboxes & Accounts</button>
        <button class="tab-btn" onclick="setTab('emails', this)">📨 Live Email Feed & Alerts</button>
        <button class="tab-btn" onclick="setTab('inventory', this)">📦 Live Inventory Stock</button>
        <button class="tab-btn" onclick="setTab('rejected', this)">🛑 Rejected Vendor Records</button>
        <button class="tab-btn" onclick="setTab('upload', this)">📤 Upload & Ingest File</button>
        <button class="tab-btn" onclick="setTab('runs', this)">📜 Execution History</button>
    </nav>

    <!-- TAB 1: MONITORED ACCOUNTS -->
    <div class="tab-pane active" id="paneAccounts">
        <div class="toolbar">
            <div style="font-size: 13px; color: var(--text-secondary);">
                Active email feeds automatically scanned in the background. Attachments are extracted and important alerts trigger Windows desktop toasts.
            </div>
            <div>
                <button class="btn btn-primary" onclick="openLinkModal()">➕ Link New Email Feed</button>
            </div>
        </div>

        <div class="accounts-grid" id="accountsGrid">
            <div style="grid-column: 1 / -1; text-align: center; color: var(--text-secondary); padding: 40px;">
                Loading monitored accounts...
            </div>
        </div>
    </div>

    <!-- TAB 2: LIVE EMAIL FEED -->
    <div class="tab-pane" id="paneEmails">
        <div class="toolbar">
            <div class="search-box">
                <input type="text" id="emailSearch" placeholder="Search emails by sender, subject, or keyword..." onkeyup="filterEmailsTable()">
            </div>
            <div class="filters">
                <button class="filter-btn active" onclick="filterEmails('all', this)">All Emails</button>
                <button class="filter-btn" onclick="filterEmails('high', this)">🚨 High Priority / Actionable</button>
                <button class="filter-btn" onclick="filterEmails('normal', this)">Normal</button>
            </div>
        </div>

        <div class="table-responsive">
            <table id="emailsTable">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Sender Name & Email</th>
                        <th>Subject</th>
                        <th>Priority Rank</th>
                        <th>Files</th>
                        <th>Status</th>
                        <th>Received Time</th>
                    </tr>
                </thead>
                <tbody id="emailsTableBody">
                    <tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 30px;">Loading emails history...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- TAB 3: LIVE INVENTORY -->
    <div class="tab-pane" id="paneInventory">
        <div class="toolbar">
            <div class="search-box">
                <input type="text" id="inventorySearch" placeholder="Search by SKU, Product Name, Vendor, or Invoice #..." onkeyup="filterInventoryTable()">
            </div>
            <div class="filters">
                <button class="filter-btn active" onclick="filterInventory('all', this)">All Items</button>
                <button class="filter-btn" onclick="filterInventory('critical', this)">Critical Shortages</button>
                <button class="filter-btn" onclick="filterInventory('warning', this)">Price Warnings</button>
                <button class="filter-btn" onclick="filterInventory('normal', this)">Healthy Stock</button>
                <a href="/api/export/inventory" class="btn btn-outline" style="padding: 5px 12px; font-size: 11px;">📥 Export CSV</a>
            </div>
        </div>

        <div class="table-responsive">
            <table id="inventoryTable">
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Vendor</th>
                        <th>SKU</th>
                        <th>Product Description</th>
                        <th style="text-align: center;">Qty</th>
                        <th style="text-align: right;">Unit Price</th>
                        <th>Invoice / PO</th>
                        <th>Alert Status</th>
                        <th>Source File</th>
                    </tr>
                </thead>
                <tbody id="inventoryTableBody">
                    <tr><td colspan="9" style="text-align: center; color: var(--text-secondary); padding: 30px;">Loading inventory records...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- TAB 4: REJECTED RECORDS AUDIT -->
    <div class="tab-pane" id="paneRejected">
        <div class="toolbar">
            <div class="search-box">
                <input type="text" id="rejectedSearch" placeholder="Search rejected records by vendor or error reason..." onkeyup="filterRejectedTable()">
            </div>
            <div>
                <a href="/api/export/rejected" class="btn btn-outline" style="padding: 5px 12px; font-size: 11px;">📥 Export Rejections CSV</a>
            </div>
        </div>

        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Vendor</th>
                        <th>Item ID</th>
                        <th>Rejection Reason</th>
                        <th>Raw Data Snippet</th>
                        <th>Source Attachment</th>
                        <th>Timestamp</th>
                    </tr>
                </thead>
                <tbody id="rejectedTableBody">
                    <tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 30px;">Loading rejected audit logs...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- TAB 5: UPLOAD & INGEST FILE -->
    <div class="tab-pane" id="paneUpload">
        <div class="dropzone-container" id="dropzone" onclick="document.getElementById('manualFileInput').click()">
            <div class="dropzone-icon">📥</div>
            <div class="dropzone-text">
                <h3>Drag & Drop Vendor File Here, or Click to Browse</h3>
                <p>Supports <strong>.CSV, .XLSX, .XLS, and .PDF</strong> (Vendor Invoices and Stock Catalogs)</p>
            </div>
            <input type="file" id="manualFileInput" class="file-input" accept=".csv,.xlsx,.xls,.pdf,.txt,.tsv" onchange="handleFileSelection(event)">
        </div>

        <div id="uploadStatusCard" style="display: none; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px;">
            <h3 id="uploadStatusTitle" style="font-size: 15px; margin-bottom: 6px;">Processing File...</h3>
            <p id="uploadStatusDetails" style="color: var(--text-secondary); font-size: 13px;"></p>
        </div>
    </div>

    <!-- TAB 6: EXECUTION HISTORY -->
    <div class="tab-pane" id="paneRuns">
        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>Run ID</th>
                        <th>Timestamp</th>
                        <th>Duration</th>
                        <th>Emails Inspected</th>
                        <th>Emails Processed</th>
                        <th>Files Ingested</th>
                        <th>Valid Items</th>
                        <th>Rejected Rows</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody id="runsTableBody">
                    <tr><td colspan="9" style="text-align: center; color: var(--text-secondary); padding: 30px;">Loading execution run history...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- MODAL: LINK NEW EMAIL ACCOUNT -->
    <div class="modal-overlay" id="linkModal">
        <div class="modal">
            <h2>➕ Link Monitored Email Account</h2>
            <p>Connect a new supplier feed, vendor inbox, or purchasing email address to monitor automatically.</p>
            <form id="linkAccountForm" onsubmit="submitLinkAccount(event)">
                <div class="form-group">
                    <label>Account / Vendor Display Name *</label>
                    <input type="text" id="linkName" required placeholder="e.g. Apex Global Supplies Feed">
                </div>
                <div class="form-group">
                    <label>Email Address to Monitor *</label>
                    <input type="email" id="linkEmail" required placeholder="e.g. vendor.apex@gmail.com">
                </div>
                <div class="form-group">
                    <label>Connection Protocol</label>
                    <select id="linkProvider">
                        <option value="GMAIL">Gmail API (OAuth 2.0)</option>
                        <option value="IMAP">Custom IMAP Inbox</option>
                        <option value="VENDOR_FEED">Supplier Inbound Feed</option>
                    </select>
                </div>
                <div class="form-check">
                    <input type="checkbox" id="linkDesktopAlerts" checked>
                    <label for="linkDesktopAlerts">Trigger Windows Desktop Alerts when important emails arrive</label>
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn btn-outline" onclick="closeLinkModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Link & Start Monitoring</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL: RESET DEMO CONFIRMATION -->
    <div class="modal-overlay" id="resetModal">
        <div class="modal">
            <h2>Reset Demo Data?</h2>
            <p>This will clear demo email logs, simulated attachments, and demo inventory items so you can re-run the demo cleanly. Production data will not be affected.</p>
            <div class="modal-actions">
                <button class="btn btn-outline" onclick="closeResetModal()">Cancel</button>
                <button class="btn btn-danger" onclick="executeResetDemo()">Yes, Reset Data</button>
            </div>
        </div>
    </div>

    <!-- Toast Notification -->
    <div class="toast" id="toast">
        <span id="toastIcon">ℹ️</span>
        <span id="toastMsg">Notification message</span>
    </div>

    <script>
        let currentFilter = 'all';
        let emailFilter = 'all';
        let inventoryData = [];
        let rejectedData = [];
        let emailsData = [];
        let accountsData = [];

        function setTab(tabId, btn) {
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            
            const targetPane = document.getElementById('pane' + tabId.charAt(0).toUpperCase() + tabId.slice(1));
            if (targetPane) targetPane.classList.add('active');

            if (btn) {
                btn.classList.add('active');
            } else {
                const buttons = document.querySelectorAll('.tab-btn');
                buttons.forEach(b => {
                    if (b.innerText.toLowerCase().includes(tabId)) b.classList.add('active');
                });
            }

            if (tabId === 'accounts') loadAccounts();
            if (tabId === 'emails') loadEmails();
            if (tabId === 'inventory') loadInventory();
            if (tabId === 'rejected') loadRejected();
            if (tabId === 'runs') loadRuns();
        }

        function showToast(message, type = 'info') {
            const toast = document.getElementById('toast');
            const icon = document.getElementById('toastIcon');
            const msg = document.getElementById('toastMsg');

            toast.className = 'toast show ' + type;
            icon.innerText = type === 'success' ? '✅' : (type === 'error' ? '❌' : 'ℹ️');
            msg.innerText = message;

            setTimeout(() => {
                toast.classList.remove('show');
            }, 4000);
        }

        // Test Windows Desktop Alert
        async function testDesktopAlert() {
            showToast("Dispatching Windows Desktop Toast Alert...", "info");
            try {
                const res = await fetch('/api/test-desktop-alert', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast("Desktop alert sent! Check your Windows notification center.", "success");
                } else {
                    showToast("Desktop alert failed: " + data.error, "error");
                }
            } catch (err) {
                showToast("Error: " + err.message, "error");
            }
        }

        // Load Status & Metrics
        async function loadStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                document.getElementById('statTotalUnits').innerText = (data.total_units_in_stock || 0).toLocaleString();
                document.getElementById('statActiveAccounts').innerText = (data.active_monitored_accounts || 0).toLocaleString();
                document.getElementById('statImportantEmails').innerText = (data.important_emails_count || 0).toLocaleString();
                document.getElementById('statCriticalAlerts').innerText = (data.critical_alerts_count || 0).toLocaleString();
                document.getElementById('statWarningAlerts').innerText = (data.warning_alerts_count || 0).toLocaleString();
            } catch (err) {
                console.error("Failed to load status:", err);
            }
        }

        // Load Monitored Accounts
        async function loadAccounts() {
            try {
                const res = await fetch('/api/monitored-accounts');
                accountsData = await res.json();
                renderAccountsGrid();
            } catch (err) {
                console.error("Failed to load accounts:", err);
            }
        }

        function renderAccountsGrid() {
            const grid = document.getElementById('accountsGrid');
            if (accountsData.length === 0) {
                grid.innerHTML = `
                    <div style="grid-column: 1 / -1; text-align: center; color: var(--text-secondary); padding: 40px; background: var(--surface); border-radius: 12px;">
                        <h3>No Email Accounts Linked Yet</h3>
                        <p style="margin-top: 6px; font-size: 13px;">Click '+ Link New Email Feed' to link vendor inboxes for automated stock & invoice monitoring.</p>
                    </div>`;
                return;
            }

            grid.innerHTML = accountsData.map(acc => {
                const isActive = acc.is_active === 1;
                const statusBadge = isActive 
                    ? `<span class="badge badge-success">&#9679; MONITORING ACTIVE</span>`
                    : `<span class="badge badge-normal">PAUSED</span>`;

                return `
                <div class="account-card ${isActive ? 'active' : 'paused'}">
                    <div class="account-header">
                        <div>
                            <div class="account-name">${escapeHtml(acc.account_name)}</div>
                            <div class="account-email">${escapeHtml(acc.email_address)}</div>
                        </div>
                        <div>${statusBadge}</div>
                    </div>
                    <div class="account-stats">
                        <div>
                            <div class="account-stat-num">${acc.total_emails_detected || 0}</div>
                            <div class="account-stat-lbl">Emails Inspected</div>
                        </div>
                        <div>
                            <div class="account-stat-num" style="color: var(--critical);">${acc.important_emails_detected || 0}</div>
                            <div class="account-stat-lbl">🚨 Important Alerts</div>
                        </div>
                    </div>
                    <div class="account-footer">
                        <span>Last check: ${acc.last_checked_at || 'Pending scan'}</span>
                        <div class="account-actions">
                            <button class="btn btn-outline" style="padding: 3px 8px; font-size: 11px;" onclick="toggleAccount(${acc.id}, ${!isActive})">
                                ${isActive ? '⏸️ Pause' : '▶️ Resume'}
                            </button>
                            <button class="btn btn-danger" style="padding: 3px 8px; font-size: 11px;" onclick="deleteAccount(${acc.id})">
                                🗑️ Unlink
                            </button>
                        </div>
                    </div>
                </div>
                `;
            }).join('');
        }

        async function toggleAccount(id, activate) {
            try {
                const res = await fetch('/api/monitored-accounts/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: id, is_active: activate })
                });
                const data = await res.json();
                if (data.success) {
                    showToast(activate ? "Monitoring resumed for account" : "Monitoring paused for account", "info");
                    loadAccounts();
                    loadStatus();
                }
            } catch (err) {
                showToast("Failed to update status: " + err.message, "error");
            }
        }

        async function deleteAccount(id) {
            if (!confirm("Are you sure you want to unlink this email account?")) return;
            try {
                const res = await fetch('/api/monitored-accounts/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: id })
                });
                const data = await res.json();
                if (data.success) {
                    showToast("Account unlinked successfully.", "success");
                    loadAccounts();
                    loadStatus();
                }
            } catch (err) {
                showToast("Failed to unlink account: " + err.message, "error");
            }
        }

        // Link Account Modal
        function openLinkModal() {
            document.getElementById('linkModal').classList.add('active');
        }
        function closeLinkModal() {
            document.getElementById('linkModal').classList.remove('active');
        }
        async function submitLinkAccount(e) {
            e.preventDefault();
            const name = document.getElementById('linkName').value.trim();
            const email = document.getElementById('linkEmail').value.trim();
            const provider = document.getElementById('linkProvider').value;
            const desktopAlerts = document.getElementById('linkDesktopAlerts').checked;

            try {
                const res = await fetch('/api/monitored-accounts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        account_name: name,
                        email_address: email,
                        provider_type: provider,
                        desktop_alerts: desktopAlerts
                    })
                });
                const data = await res.json();
                if (data.success) {
                    showToast(`Linked '${name}'! Monitoring is now active.`, "success");
                    closeLinkModal();
                    document.getElementById('linkAccountForm').reset();
                    loadAccounts();
                    loadStatus();
                } else {
                    showToast("Failed to link email: " + data.error, "error");
                }
            } catch (err) {
                showToast("Error linking email: " + err.message, "error");
            }
        }

        // Load Emails Feed
        async function loadEmails() {
            try {
                const res = await fetch('/api/emails');
                emailsData = await res.json();
                renderEmailsTable();
            } catch (err) {
                console.error("Failed to load emails:", err);
            }
        }

        function filterEmails(filter, btn) {
            emailFilter = filter;
            if (btn) {
                document.querySelectorAll('#paneEmails .filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            }
            renderEmailsTable();
        }

        function renderEmailsTable() {
            const tbody = document.getElementById('emailsTableBody');
            const query = (document.getElementById('emailSearch').value || '').toLowerCase();

            const filtered = emailsData.filter(em => {
                const isHigh = (em.priority || '').toLowerCase().includes('high');
                const matchesFilter = (emailFilter === 'all') ||
                    (emailFilter === 'high' && isHigh) ||
                    (emailFilter === 'normal' && !isHigh);

                const text = `${em.sender_name} ${em.sender_email} ${em.subject}`.toLowerCase();
                const matchesSearch = text.includes(query);

                return matchesFilter && matchesSearch;
            });

            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 30px;">No incoming emails match filter.</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map((em, idx) => {
                const isHigh = (em.priority || '').toLowerCase().includes('high');
                const rowClass = isHigh ? 'email-high-priority' : '';
                const badgeHtml = isHigh 
                    ? `<span class="badge badge-high">🚨 HIGH PRIORITY: ACTION REQUIRED</span>`
                    : `<span class="badge badge-normal">${escapeHtml(em.priority || 'Normal')}</span>`;

                return `
                <tr class="${rowClass}">
                    <td data-label="#"><strong>${idx + 1}</strong></td>
                    <td data-label="Sender">
                        <div><strong>${escapeHtml(em.sender_name || 'Vendor')}</strong></div>
                        <div style="font-family: monospace; font-size: 11px; color: var(--text-secondary);">${escapeHtml(em.sender_email)}</div>
                    </td>
                    <td data-label="Subject">
                        <strong>${escapeHtml(em.subject)}</strong>
                        <div style="color: var(--text-secondary); font-size: 11px; max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(em.body_preview || '')}</div>
                    </td>
                    <td data-label="Priority">${badgeHtml}</td>
                    <td data-label="Files" style="text-align: center; font-weight: 700;">${em.attachment_count || 0}</td>
                    <td data-label="Status"><span class="badge ${em.status === 'SUCCESS' ? 'badge-success' : 'badge-normal'}">${em.status}</span></td>
                    <td data-label="Received" style="font-size: 11px; color: var(--text-secondary);">${em.received_at || ''}</td>
                </tr>
                `;
            }).join('');
        }

        function filterEmailsTable() {
            renderEmailsTable();
        }

        // Load Inventory Items
        async function loadInventory() {
            try {
                const res = await fetch('/api/inventory');
                inventoryData = await res.json();
                renderInventoryTable();
            } catch (err) {
                console.error("Failed to load inventory:", err);
            }
        }

        function filterInventory(filter, btn) {
            currentFilter = filter;
            if (btn) {
                document.querySelectorAll('#paneInventory .filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            }
            renderInventoryTable();
        }

        function renderInventoryTable() {
            const tbody = document.getElementById('inventoryTableBody');
            const query = (document.getElementById('inventorySearch').value || '').toLowerCase();

            const filtered = inventoryData.filter(item => {
                const matchesFilter = (currentFilter === 'all') || 
                    (currentFilter === 'critical' && item.alert_severity === 'CRITICAL') ||
                    (currentFilter === 'warning' && item.alert_severity === 'WARNING') ||
                    (currentFilter === 'normal' && item.alert_severity === 'NORMAL');

                const text = `${item.vendor_name} ${item.sku} ${item.product_name} ${item.invoice_no}`.toLowerCase();
                const matchesSearch = text.includes(query);

                return matchesFilter && matchesSearch;
            });

            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-secondary); padding: 30px;">No matching inventory items found.</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map((item, idx) => {
                let badgeClass = 'badge-normal';
                let badgeText = 'OK / IN STOCK';
                let rowClass = '';

                if (item.quantity === 0) {
                    badgeClass = 'badge-critical';
                    badgeText = 'CRITICAL: OUT OF STOCK';
                    rowClass = 'table-danger';
                } else if (item.quantity <= 10) {
                    badgeClass = 'badge-critical';
                    badgeText = `CRITICAL: LOW STOCK (${item.quantity})`;
                    rowClass = 'table-danger';
                } else if (item.price >= 500) {
                    badgeClass = 'badge-warning';
                    badgeText = `WARNING: HIGH VALUE ($${item.price.toFixed(2)})`;
                    rowClass = 'table-warning';
                }

                return `
                <tr class="${rowClass}">
                    <td data-label="#"><strong>${idx + 1}</strong></td>
                    <td data-label="Vendor"><span class="tag-vendor">${escapeHtml(item.vendor_name)}</span></td>
                    <td data-label="SKU"><code>${escapeHtml(item.sku)}</code></td>
                    <td data-label="Product"><strong>${escapeHtml(item.product_name)}</strong></td>
                    <td data-label="Quantity" style="text-align: center; font-weight: 700;">${item.quantity}</td>
                    <td data-label="Price" style="text-align: right; font-family: monospace;">$${item.price.toFixed(2)}</td>
                    <td data-label="Invoice"><span class="tag-invoice">${escapeHtml(item.invoice_no || 'N/A')}</span></td>
                    <td data-label="Alert"><span class="badge ${badgeClass}">${badgeText}</span></td>
                    <td data-label="Source"><span class="source-name" title="${escapeHtml(item.source_file || '')}">${escapeHtml(item.source_file || 'Attachment')}</span></td>
                </tr>
                `;
            }).join('');
        }

        function filterInventoryTable() {
            renderInventoryTable();
        }

        // Load Rejected Records
        async function loadRejected() {
            try {
                const res = await fetch('/api/rejected');
                rejectedData = await res.json();
                renderRejectedTable();
            } catch (err) {
                console.error("Failed to load rejections:", err);
            }
        }

        function renderRejectedTable() {
            const tbody = document.getElementById('rejectedTableBody');
            const query = (document.getElementById('rejectedSearch').value || '').toLowerCase();

            const filtered = rejectedData.filter(item => {
                const text = `${item.vendor_name} ${item.rejection_reason} ${item.product_id}`.toLowerCase();
                return text.includes(query);
            });

            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 30px;">No rejected records found. All data is valid!</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map(item => `
                <tr>
                    <td data-label="ID">#${item.id}</td>
                    <td data-label="Vendor"><span class="tag-vendor">${escapeHtml(item.vendor_name || 'Unknown')}</span></td>
                    <td data-label="Item ID"><code>${escapeHtml(item.product_id || 'N/A')}</code></td>
                    <td data-label="Reason"><span class="badge badge-critical">${escapeHtml(item.rejection_reason)}</span></td>
                    <td data-label="Raw Snippet" style="font-family: monospace; font-size: 11px; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(item.raw_data || '')}</td>
                    <td data-label="Source"><span class="source-name">${escapeHtml(item.source_file || '')}</span></td>
                    <td data-label="Date" style="color: var(--text-secondary); font-size: 11px;">${item.created_at || ''}</td>
                </tr>
            `).join('');
        }

        function filterRejectedTable() {
            renderRejectedTable();
        }

        // Load Execution Runs
        async function loadRuns() {
            try {
                const res = await fetch('/api/runs');
                const runs = await res.json();
                const tbody = document.getElementById('runsTableBody');

                if (runs.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-secondary); padding: 30px;">No execution runs logged yet.</td></tr>`;
                    return;
                }

                tbody.innerHTML = runs.map(r => `
                    <tr>
                        <td data-label="Run ID">#${r.id}</td>
                        <td data-label="Timestamp">${r.run_timestamp}</td>
                        <td data-label="Duration">${(r.duration_seconds || 0).toFixed(1)}s</td>
                        <td data-label="Inspected">${r.emails_inspected}</td>
                        <td data-label="Processed">${r.emails_processed}</td>
                        <td data-label="Files">${r.files_downloaded}</td>
                        <td data-label="Valid">${r.records_valid}</td>
                        <td data-label="Rejected" style="color: ${r.records_rejected > 0 ? 'var(--critical)' : 'inherit'}; font-weight: bold;">${r.records_rejected}</td>
                        <td data-label="Status"><span class="badge ${r.status === 'SUCCESS' ? 'badge-success' : 'badge-warning'}">${r.status}</span></td>
                    </tr>
                `).join('');
            } catch (err) {
                console.error("Failed to load runs:", err);
            }
        }

        // Trigger Pipeline Execution
        async function triggerPipeline(demoMode = false) {
            const btn = demoMode ? document.getElementById('btnRunDemo') : document.getElementById('btnRunLive');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = `<span class="spinner"></span> Scanning...`;

            showToast(demoMode ? "Starting Simulated Ingestion Scan..." : "Scanning Linked Inboxes via Gmail API...", "info");

            try {
                const res = await fetch('/api/run-pipeline', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ demo_mode: demoMode })
                });
                const data = await res.json();

                if (data.success) {
                    showToast(`Scan complete! ${data.valid_records} items ingested (${data.rejected_records} rejected). Desktop alert triggered for important updates.`, 'success');
                    loadStatus();
                    loadAccounts();
                    loadEmails();
                    loadInventory();
                    loadRejected();
                    loadRuns();
                } else {
                    showToast(`Pipeline Failed: ${data.error || 'Unknown error'}`, 'error');
                }
            } catch (err) {
                showToast(`Error running pipeline: ${err.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }

        // Reset Demo Modal & Execution
        function confirmResetDemo() {
            document.getElementById('resetModal').classList.add('active');
        }
        function closeResetModal() {
            document.getElementById('resetModal').classList.remove('active');
        }
        async function executeResetDemo() {
            closeResetModal();
            showToast("Resetting demo data...", "info");
            try {
                const res = await fetch('/api/reset-demo', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast("Demo data successfully reset!", "success");
                    loadStatus();
                    loadAccounts();
                    loadEmails();
                    loadInventory();
                    loadRejected();
                } else {
                    showToast("Failed to reset demo data", "error");
                }
            } catch (err) {
                showToast("Error resetting data: " + err.message, "error");
            }
        }

        // Manual File Upload Handler
        function handleFileSelection(event) {
            const file = event.target.files[0];
            if (!file) return;
            uploadFile(file);
        }

        const dropzone = document.getElementById('dropzone');
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('drag-over');
        });
        dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                uploadFile(e.dataTransfer.files[0]);
            }
        });

        async function uploadFile(file) {
            const statusCard = document.getElementById('uploadStatusCard');
            const statusTitle = document.getElementById('uploadStatusTitle');
            const statusDetails = document.getElementById('uploadStatusDetails');

            statusCard.style.display = 'block';
            statusTitle.innerHTML = `<span class="spinner"></span> Processing '${file.name}' (${(file.size / 1024).toFixed(1)} KB)...`;
            statusDetails.innerText = "Parsing columns, extracting SKU, validating price & quantity...";

            const reader = new FileReader();
            reader.onload = async function(e) {
                const base64Data = e.target.result.split(',')[1];
                try {
                    const res = await fetch('/api/upload', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            filename: file.name,
                            file_data: base64Data
                        })
                    });
                    const result = await res.json();
                    if (result.success) {
                        statusTitle.innerHTML = `✅ Successfully Processed: ${file.name}`;
                        statusDetails.innerHTML = `Ingested <strong>${result.valid_count}</strong> valid items. <strong>${result.rejected_count}</strong> rows rejected during audit.`;
                        showToast(`File processed! ${result.valid_count} records saved.`, 'success');
                        loadStatus();
                        loadInventory();
                    } else {
                        statusTitle.innerHTML = `❌ Processing Failed: ${file.name}`;
                        statusDetails.innerText = result.error || 'Failed to process file.';
                        showToast(`Failed to process file: ${result.error}`, 'error');
                    }
                } catch (err) {
                    statusTitle.innerHTML = `❌ Error Uploading File`;
                    statusDetails.innerText = err.message;
                    showToast(`Upload error: ${err.message}`, 'error');
                }
            };
            reader.readAsDataURL(file);
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }

        // Periodic Background Status Refresh (every 10 seconds)
        window.addEventListener('DOMContentLoaded', () => {
            loadStatus();
            loadAccounts();
            setInterval(loadStatus, 10000);
        });
    </script>
</body>
</html>
"""


# ==============================================================================
# Request Handler
# ==============================================================================

class AutomationRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP Request Handler serving the Web App UI and REST API.
    """

    def log_message(self, format: str, *args: Any) -> None:
        """Suppresses noisy standard library access logs to console."""
        logger.debug("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        """Sends a JSON response with proper CORS headers."""
        response_bytes = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(response_bytes)

    def _send_html(self, html_content: str, status: int = HTTPStatus.OK) -> None:
        """Sends an HTML response."""
        response_bytes = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _send_csv(self, filename: str, csv_content: str) -> None:
        """Sends a downloadable CSV attachment."""
        response_bytes = csv_content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_OPTIONS(self) -> None:
        """Handles pre-flight CORS requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        """Handles GET requests for UI and data retrieval."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        db = DatabaseService(DATABASE_PATH)

        if path in ("/", "/index.html"):
            self._send_html(SPA_HTML_TEMPLATE)
            return

        if path == "/api/status":
            stats = db.get_summary_statistics()
            items = db.get_all_inventory_with_alerts(
                low_stock_threshold=LOW_STOCK_ALERT_THRESHOLD,
                price_spike_threshold=PRICE_SPIKE_ALERT_THRESHOLD,
            )
            stats["critical_alerts_count"] = sum(1 for it in items if it.get("alert_severity") == "CRITICAL")
            stats["warning_alerts_count"] = sum(1 for it in items if it.get("alert_severity") == "WARNING")
            
            monitored = db.get_monitored_accounts()
            stats["active_monitored_accounts"] = sum(1 for a in monitored if a.get("is_active", 1))
            stats["important_emails_count"] = sum(a.get("important_emails_detected", 0) for a in monitored)
            self._send_json(stats)
            return

        if path == "/api/monitored-accounts":
            accounts = db.get_monitored_accounts()
            if not accounts:
                db.seed_default_monitored_accounts()
                accounts = db.get_monitored_accounts()
            self._send_json(accounts)
            return

        if path == "/api/emails":
            emails = db.get_all_emails_history(limit=50)
            self._send_json(emails)
            return

        if path == "/api/inventory":
            items = db.get_all_inventory_with_alerts(
                low_stock_threshold=LOW_STOCK_ALERT_THRESHOLD,
                price_spike_threshold=PRICE_SPIKE_ALERT_THRESHOLD,
            )
            self._send_json(items)
            return

        if path == "/api/rejected":
            rejected = db.get_latest_rejected_records(limit=100)
            self._send_json(rejected)
            return

        if path == "/api/runs":
            runs = db.get_processing_runs(limit=30)
            self._send_json(runs)
            return

        if path == "/api/vendors":
            vendors = db.get_all_vendors()
            self._send_json(vendors)
            return

        if path == "/api/export/inventory":
            items = db.get_all_inventory_with_alerts(
                low_stock_threshold=LOW_STOCK_ALERT_THRESHOLD,
                price_spike_threshold=PRICE_SPIKE_ALERT_THRESHOLD,
            )
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Vendor", "SKU", "Product Name", "Quantity", "Price", "Invoice No", "Alert Status", "Source File", "Updated Date"])
            for it in items:
                writer.writerow([
                    it.get("vendor_name"),
                    it.get("sku"),
                    it.get("product_name"),
                    it.get("quantity"),
                    it.get("price"),
                    it.get("invoice_no"),
                    it.get("alert_severity"),
                    it.get("source_file"),
                    it.get("updated_date"),
                ])
            filename = f"inventory_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self._send_csv(filename, output.getvalue())
            return

        if path == "/api/export/rejected":
            rejected = db.get_latest_rejected_records(limit=500)
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "Vendor", "Product ID", "Rejection Reason", "Raw Data", "Source File", "Created At"])
            for r in rejected:
                writer.writerow([
                    r.get("id"),
                    r.get("vendor_name"),
                    r.get("product_id"),
                    r.get("rejection_reason"),
                    r.get("raw_data"),
                    r.get("source_file"),
                    r.get("created_at"),
                ])
            filename = f"rejected_audit_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self._send_csv(filename, output.getvalue())
            return

        self._send_json({"error": "Endpoint not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Handles action triggers: link account, run pipeline, upload file, reset data."""
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Read JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except Exception:
            payload = {}

        db = DatabaseService(DATABASE_PATH)

        if path == "/api/monitored-accounts":
            name = payload.get("account_name", "").strip()
            email = payload.get("email_address", "").strip()
            provider = payload.get("provider_type", "GMAIL")
            alerts = payload.get("desktop_alerts", True)

            if not name or not email or "@" not in email:
                self._send_json({"success": False, "error": "Please provide a valid name and email address."}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                acc_id = db.add_monitored_account(
                    account_name=name,
                    email_address=email,
                    provider_type=provider,
                    desktop_alerts=alerts,
                )
                logger.info("Linked new monitored account '%s' (%s)", name, email)
                self._send_json({"success": True, "id": acc_id, "message": f"Successfully linked {email} for monitoring."})
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/monitored-accounts/toggle":
            acc_id = int(payload.get("id", 0))
            is_active = bool(payload.get("is_active", True))
            ok = db.toggle_monitored_account(acc_id, is_active)
            self._send_json({"success": ok})
            return

        if path == "/api/monitored-accounts/delete":
            acc_id = int(payload.get("id", 0))
            ok = db.delete_monitored_account(acc_id)
            self._send_json({"success": ok})
            return

        if path == "/api/test-desktop-alert":
            try:
                notifier = NotificationService(db_path=DATABASE_PATH)
                ok = notifier.send_desktop_notification(
                    sender_name="System Test Notification",
                    subject="Windows Desktop Alerts are active & working properly!",
                    priority="High Priority",
                )
                self._send_json({"success": ok, "message": "Desktop alert triggered successfully."})
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/run-pipeline":
            demo_mode = payload.get("demo_mode", False)
            try:
                from main import run_pipeline
                metrics = run_pipeline(demo_mode=demo_mode, open_dashboard=False)
                self._send_json({
                    "success": True,
                    "emails_inspected": metrics.emails_inspected,
                    "emails_processed": metrics.emails_processed,
                    "valid_records": metrics.total_valid_records,
                    "rejected_records": metrics.total_rejected_records,
                    "duration_seconds": metrics.duration_seconds,
                    "status": metrics.overall_status,
                })
            except Exception as exc:
                logger.exception("Error executing pipeline via Web API: %s", exc)
                self._send_json({"success": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/reset-demo":
            try:
                db.reset_demo_data()
                self._send_json({"success": True, "message": "Demo data successfully reset."})
            except Exception as exc:
                self._send_json({"success": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path == "/api/upload":
            raw_filename = payload.get("filename", "uploaded_file.csv")
            b64_data = payload.get("file_data", "")

            if not b64_data:
                self._send_json({"success": False, "error": "No file content received"}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                file_bytes = base64.b64decode(b64_data)
                clean_name = re.sub(r"[^\w\-.]", "_", Path(raw_filename).name)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = DOWNLOADS_DIR / f"{timestamp}_ManualUpload_{clean_name}"

                with open(save_path, "wb") as f:
                    f.write(file_bytes)

                processor = InventoryProcessor()
                result = processor.process_file(file_path=save_path, fallback_vendor_name="Direct Upload")

                valid_saved = 0
                if result.valid_records:
                    valid_saved, _ = db.upsert_inventory_records(
                        records=result.valid_records,
                        source_file=save_path.name,
                    )

                if result.rejected_records:
                    db.record_rejected_records(
                        rejected_rows=result.rejected_records,
                        source_file=save_path.name,
                    )

                self._send_json({
                    "success": result.success,
                    "filename": save_path.name,
                    "valid_count": valid_saved,
                    "rejected_count": result.rejected_count,
                    "error": result.error_message,
                })
            except Exception as exc:
                logger.exception("Error processing uploaded file: %s", exc)
                self._send_json({"success": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json({"error": "POST endpoint not found"}, status=HTTPStatus.NOT_FOUND)


# ==============================================================================
# Web Server Lifecycle & Background Poller
# ==============================================================================

def _background_monitor_worker() -> None:
    """
    Background worker thread that automatically runs monitoring cycles
    for all linked monitored accounts while the web portal is running.
    """
    time.sleep(5)  # Wait for server to stabilize
    while auto_monitor_state.is_running:
        try:
            from main import run_pipeline
            # Runs pipeline to inspect all active monitored inboxes
            run_pipeline(demo_mode=False, open_dashboard=False)
            auto_monitor_state.last_run_time = datetime.now()
            auto_monitor_state.last_status = "SUCCESS"
        except Exception as exc:
            auto_monitor_state.last_status = "ERROR"
            auto_monitor_state.last_summary = str(exc)
            logger.debug("Background auto-monitor pass completed/skipped: %s", exc)

        for _ in range(auto_monitor_state.poll_interval_seconds):
            if not auto_monitor_state.is_running:
                break
            time.sleep(1)


def start_web_server(port: int = 8080, open_browser: bool = True, host: str = "0.0.0.0") -> None:
    """
    Starts the threaded web application server and optionally launches browser.
    Listens on 0.0.0.0 to support local machine, office LAN, and Docker deployment.
    """
    # Seed default monitored accounts if empty
    db = DatabaseService(DATABASE_PATH)
    db.seed_default_monitored_accounts()

    # Check if port is in use; if so, try sequential ports
    for p in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer((host, p), AutomationRequestHandler)
            port = p
            break
        except OSError:
            continue
    else:
        logger.error("Could not bind to any port between %d and %d", port, port + 10)
        print(f"[ERROR] Could not start server on {host}:{port}")
        return

    display_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{display_host}:{port}"
    print("\n" + "=" * 72)
    print("      SMART VENDOR EMAIL & INVENTORY - MULTI-ACCOUNT WEB PORTAL")
    print("=" * 72)
    print(f"  Status          : ONLINE & AUTO-MONITORING ACTIVE")
    print(f"  Web Portal URL  : {url}")
    print(f"  Multi-Email     : Link, monitor & alert for multiple vendor inboxes")
    print(f"  Desktop Alerts  : Enabled via Plyer (Windows Toasts)")
    print("------------------------------------------------------------------------")
    print("  Press Ctrl+C in this terminal anytime to stop the server.")
    print("=" * 72 + "\n")

    # Start background auto-monitor worker thread
    bg_thread = threading.Thread(target=_background_monitor_worker, daemon=True)
    bg_thread.start()

    if open_browser:
        def _open():
            time.sleep(0.6)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Employee Web Portal...")
    finally:
        auto_monitor_state.is_running = False
        httpd.server_close()
        print("Web server stopped successfully.\n")


if __name__ == "__main__":
    start_web_server()
