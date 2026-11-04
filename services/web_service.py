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
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <meta name="theme-color" content="#060913">
    <title>Smart Vendor Email & Inventory Portal</title>
    <!-- Google Fonts: Plus Jakarta Sans & JetBrains Mono -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #060913;
            --surface: rgba(15, 23, 42, 0.72);
            --surface-hover: rgba(30, 41, 68, 0.85);
            --border: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(59, 130, 246, 0.45);
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
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        html {
            width: 100%;
            max-width: 100%;
            overflow-x: hidden !important;
            -webkit-text-size-adjust: 100%;
        }
        body {
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            background-image: 
                radial-gradient(circle at 50% 0%, rgba(59, 130, 246, 0.22) 0%, transparent 55%),
                radial-gradient(circle at 85% 25%, rgba(139, 92, 246, 0.15) 0%, transparent 45%),
                radial-gradient(circle at 15% 65%, rgba(16, 185, 129, 0.1) 0%, transparent 45%),
                radial-gradient(circle at 75% 85%, rgba(245, 158, 11, 0.08) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            line-height: 1.5;
            padding: 20px 28px 60px 28px;
            width: 100%;
            max-width: 100%;
            overflow-x: hidden !important;
            position: relative;
            animation: fadeInPage 0.4s ease-out;
        }
        @keyframes fadeInPage {
            from { opacity: 0; transform: translateY(-4px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Top Bar */
        .navbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            padding: 18px 22px;
            background: rgba(15, 23, 42, 0.65);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: 18px;
            margin-bottom: 22px;
            box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .brand-icon {
            font-size: 26px;
            background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 50%, #ec4899 100%);
            width: 50px;
            height: 50px;
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 6px 20px rgba(59, 130, 246, 0.45);
            transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.35s;
            flex-shrink: 0;
        }
        .brand-icon:hover {
            transform: scale(1.08) rotate(4deg);
            box-shadow: 0 8px 28px rgba(139, 92, 246, 0.55);
        }
        .brand-text h1 {
            font-size: 21px;
            font-weight: 800;
            letter-spacing: -0.025em;
            background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 60%, #94a3b8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .brand-text p {
            font-size: 12px;
            color: var(--text-secondary);
            font-weight: 500;
        }
        .nav-wrapper {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .nav-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }

        /* Auto-Monitor Status Indicator with Dual Radar Wave */
        .monitor-pill {
            display: inline-flex;
            align-items: center;
            gap: 9px;
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.4);
            color: #34d399;
            padding: 7px 16px;
            border-radius: 24px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.02em;
            box-shadow: 0 0 20px rgba(16, 185, 129, 0.2);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }
        .pulse-container {
            position: relative;
            width: 12px;
            height: 12px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #10b981;
            box-shadow: 0 0 10px #10b981;
            z-index: 2;
        }
        .pulse-ring {
            position: absolute;
            width: 18px;
            height: 18px;
            border-radius: 50%;
            border: 2px solid #10b981;
            animation: radarWave 1.8s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
        }
        @keyframes radarWave {
            0% { transform: scale(0.4); opacity: 1; }
            100% { transform: scale(1.8); opacity: 0; }
        }

        /* Premium Buttons with Continuous Shimmer & Neon Pulse */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 7px;
            padding: 9px 16px;
            border-radius: 10px;
            font-size: 12.5px;
            font-weight: 700;
            cursor: pointer;
            border: 1px solid transparent;
            transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
            text-decoration: none;
            white-space: nowrap;
            position: relative;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
        }
        /* Continuous Shimmer Light Beam Effect */
        .btn::after {
            content: '';
            position: absolute;
            top: -50%;
            left: -120%;
            width: 50%;
            height: 200%;
            background: linear-gradient(
                90deg, 
                transparent 0%, 
                rgba(255, 255, 255, 0.25) 50%, 
                rgba(255, 255, 255, 0.45) 75%, 
                transparent 100%
            );
            transform: rotate(25deg);
            pointer-events: none;
        }
        .btn-primary::after {
            animation: btnShimmerSweep 3.2s cubic-bezier(0.4, 0, 0.2, 1) infinite;
            -webkit-animation: btnShimmerSweep 3.2s cubic-bezier(0.4, 0, 0.2, 1) infinite;
        }
        .btn-demo::after {
            animation: btnShimmerSweep 3.2s cubic-bezier(0.4, 0, 0.2, 1) infinite 1.6s;
            -webkit-animation: btnShimmerSweep 3.2s cubic-bezier(0.4, 0, 0.2, 1) infinite 1.6s;
        }
        @keyframes btnShimmerSweep {
            0% { left: -120%; }
            35%, 100% { left: 200%; }
        }
        @-webkit-keyframes btnShimmerSweep {
            0% { left: -120%; }
            35%, 100% { left: 200%; }
        }

        .btn:hover {
            transform: translateY(-2px);
            filter: brightness(1.1);
        }
        .btn:active {
            transform: translateY(1px) scale(0.98);
        }
        .btn-primary {
            background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
            color: #fff;
            box-shadow: 0 4px 18px rgba(59, 130, 246, 0.4);
            animation: pulseBtnPrimary 3s ease-in-out infinite;
            -webkit-animation: pulseBtnPrimary 3s ease-in-out infinite;
        }
        @keyframes pulseBtnPrimary {
            0%, 100% {
                box-shadow: 0 4px 16px rgba(59, 130, 246, 0.4), 0 0 10px rgba(59, 130, 246, 0.2);
            }
            50% {
                box-shadow: 0 6px 24px rgba(59, 130, 246, 0.75), 0 0 20px rgba(59, 130, 246, 0.5);
            }
        }
        @-webkit-keyframes pulseBtnPrimary {
            0%, 100% {
                box-shadow: 0 4px 16px rgba(59, 130, 246, 0.4), 0 0 10px rgba(59, 130, 246, 0.2);
            }
            50% {
                box-shadow: 0 6px 24px rgba(59, 130, 246, 0.75), 0 0 20px rgba(59, 130, 246, 0.5);
            }
        }
        .btn-demo {
            background: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%);
            color: #fff;
            box-shadow: 0 4px 18px rgba(139, 92, 246, 0.4);
            animation: pulseBtnDemo 3s ease-in-out infinite 1.5s;
            -webkit-animation: pulseBtnDemo 3s ease-in-out infinite 1.5s;
        }
        @keyframes pulseBtnDemo {
            0%, 100% {
                box-shadow: 0 4px 16px rgba(139, 92, 246, 0.4), 0 0 10px rgba(139, 92, 246, 0.2);
            }
            50% {
                box-shadow: 0 6px 24px rgba(139, 92, 246, 0.75), 0 0 20px rgba(139, 92, 246, 0.5);
            }
        }
        @-webkit-keyframes pulseBtnDemo {
            0%, 100% {
                box-shadow: 0 4px 16px rgba(139, 92, 246, 0.4), 0 0 10px rgba(139, 92, 246, 0.2);
            }
            50% {
                box-shadow: 0 6px 24px rgba(139, 92, 246, 0.75), 0 0 20px rgba(139, 92, 246, 0.5);
            }
        }
        .btn-outline {
            background: rgba(19, 28, 46, 0.7);
            color: var(--text-primary);
            border-color: rgba(255, 255, 255, 0.12);
            backdrop-filter: blur(8px);
        }
        .btn-outline:hover {
            background: rgba(30, 42, 68, 0.9);
            border-color: rgba(59, 130, 246, 0.55);
            color: #fff;
            box-shadow: 0 4px 20px rgba(59, 130, 246, 0.25);
        }
        .btn-danger {
            background: rgba(239, 68, 68, 0.14);
            color: #f87171;
            border-color: rgba(239, 68, 68, 0.35);
        }
        .btn-danger:hover {
            background: rgba(239, 68, 68, 0.25);
            border-color: rgba(239, 68, 68, 0.65);
            color: #fff;
            box-shadow: 0 4px 20px rgba(239, 68, 68, 0.35);
        }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }

        /* Real-Time Pipeline Health Ribbon (Enterprise Visual Metric) */
        .health-ribbon {
            background: rgba(15, 23, 42, 0.65);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 12px 18px;
            margin-bottom: 20px;
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            display: flex;
            flex-direction: column;
            gap: 9px;
            box-shadow: 0 6px 24px -10px rgba(0, 0, 0, 0.4);
        }
        .health-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            flex-wrap: wrap;
            gap: 8px;
        }
        .health-meta-left {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text-primary);
        }
        .health-pill-pulse {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #10b981;
            box-shadow: 0 0 8px #10b981;
            animation: pulseDot 1.5s infinite;
        }
        @keyframes pulseDot {
            0%, 100% { opacity: 0.5; transform: scale(0.9); }
            50% { opacity: 1; transform: scale(1.2); }
        }
        .health-meta-right {
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #60a5fa;
            background: rgba(59, 130, 246, 0.1);
            padding: 2px 8px;
            border-radius: 6px;
            border: 1px solid rgba(59, 130, 246, 0.25);
        }
        .health-bar-container {
            display: flex;
            height: 7px;
            width: 100%;
            border-radius: 10px;
            overflow: hidden;
            background: rgba(255, 255, 255, 0.05);
            box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.5);
        }
        .health-bar-segment {
            height: 100%;
            transition: width 0.6s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .seg-healthy { background: linear-gradient(90deg, #10b981, #34d399); }
        .seg-warning { background: linear-gradient(90deg, #f59e0b, #fbbf24); }
        .seg-critical { background: linear-gradient(90deg, #ef4444, #f87171); }
        .health-legend {
            display: flex;
            gap: 16px;
            font-size: 11.5px;
            color: var(--text-secondary);
            flex-wrap: wrap;
        }
        .legend-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 5px;
            vertical-align: middle;
        }
        .dot-healthy { background: #10b981; box-shadow: 0 0 6px #10b981; }
        .dot-warning { background: #f59e0b; box-shadow: 0 0 6px #f59e0b; }
        .dot-critical { background: #ef4444; box-shadow: 0 0 6px #ef4444; }

        /* Stat Cards Ribbon with 3D Depth */
        .stats-ribbon {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 14px;
            margin-bottom: 22px;
        }
        .stat-card {
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 16px 18px;
            display: flex;
            flex-direction: column;
            gap: 5px;
            cursor: pointer;
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
            overflow: hidden;
            box-shadow: 0 6px 24px -8px rgba(0, 0, 0, 0.35);
        }
        .stat-card::before {
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0; height: 2px;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
            opacity: 0.7;
            transition: opacity 0.3s;
        }
        .stat-card:hover {
            transform: translateY(-4px);
            border-color: rgba(59, 130, 246, 0.5);
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.45), 0 0 24px rgba(59, 130, 246, 0.2);
        }
        .stat-card:hover::before {
            opacity: 1;
            background: linear-gradient(90deg, transparent, rgba(59, 130, 246, 0.8), transparent);
        }
        .stat-card.critical {
            border-color: rgba(239, 68, 68, 0.35);
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.85) 50%, rgba(239, 68, 68, 0.16) 100%);
        }
        .stat-card.critical:hover {
            border-color: rgba(239, 68, 68, 0.75);
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.45), 0 0 28px rgba(239, 68, 68, 0.3);
        }
        .stat-card.warning {
            border-color: rgba(245, 158, 11, 0.35);
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.85) 50%, rgba(245, 158, 11, 0.16) 100%);
        }
        .stat-card.warning:hover {
            border-color: rgba(245, 158, 11, 0.75);
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.45), 0 0 28px rgba(245, 158, 11, 0.3);
        }
        .stat-card.highlight {
            border-color: rgba(139, 92, 246, 0.35);
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.85) 50%, rgba(139, 92, 246, 0.16) 100%);
        }
        .stat-card.highlight:hover {
            border-color: rgba(139, 92, 246, 0.75);
            box-shadow: 0 14px 32px rgba(0, 0, 0, 0.45), 0 0 28px rgba(139, 92, 246, 0.3);
        }
        .stat-label {
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 700;
            color: var(--text-secondary);
            letter-spacing: 0.05em;
        }
        .stat-value {
            font-size: 26px;
            font-weight: 800;
            line-height: 1.1;
            letter-spacing: -0.02em;
            background: linear-gradient(135deg, #ffffff 0%, #cbd5e1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .stat-desc {
            font-size: 11px;
            color: var(--text-secondary);
        }

        /* Tabs */
        .tabs {
            display: flex;
            gap: 8px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 20px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            padding-bottom: 4px;
        }
        .tab-btn {
            background: transparent;
            color: var(--text-secondary);
            border: none;
            border-bottom: 2px solid transparent;
            padding: 11px 18px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.2s;
            border-radius: 10px 10px 0 0;
        }
        .tab-btn:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.03); }
        .tab-btn.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
            background: rgba(59, 130, 246, 0.08);
        }

        /* Tab Content Panes */
        .tab-pane { display: none; animation: tabPaneIn 0.25s ease-out; }
        .tab-pane.active { display: block; }
        @keyframes tabPaneIn {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Toolbar */
        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            background: var(--surface);
            padding: 12px 18px;
            border-radius: 14px;
            border: 1px solid var(--border);
            margin-bottom: 16px;
            backdrop-filter: blur(12px);
        }
        .search-box {
            flex: 1;
            min-width: 240px;
        }
        .search-box input {
            width: 100%;
            padding: 9px 14px;
            border-radius: 9px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text-primary);
            font-size: 13px;
            outline: none;
            transition: all 0.2s;
        }
        .search-box input:focus {
            border-color: var(--accent);
            box-shadow: 0 0 12px rgba(59, 130, 246, 0.35);
        }
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
            padding: 6px 13px;
            border-radius: 20px;
            font-size: 11.5px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .filter-btn.active, .filter-btn:hover {
            background: var(--accent);
            color: #fff;
            border-color: var(--accent);
            box-shadow: 0 2px 10px rgba(59, 130, 246, 0.4);
        }

        /* Cards Grid for Monitored Accounts */
        .accounts-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
            width: 100%;
            box-sizing: border-box;
        }
        .account-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            position: relative;
            transition: all 0.2s;
            backdrop-filter: blur(12px);
            overflow: hidden;
            box-sizing: border-box;
            width: 100%;
            max-width: 100%;
        }
        .account-card.active {
            border-left: 4px solid var(--success);
        }
        .account-card.paused {
            border-left: 4px solid #64748b;
            opacity: 0.78;
        }
        .account-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 10px;
            flex-wrap: wrap;
            width: 100%;
            box-sizing: border-box;
        }
        .account-info {
            flex: 1 1 170px;
            min-width: 0;
            overflow: hidden;
        }
        .account-name {
            font-size: 15px;
            font-weight: 700;
            color: #fff;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            line-height: 1.3;
        }
        .account-email {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 3px;
            word-break: break-all;
            overflow-wrap: anywhere;
            line-height: 1.3;
        }
        .account-badge-wrap {
            flex-shrink: 0;
            display: flex;
            align-items: center;
        }
        .badge-account-status {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 8px;
            font-size: 10.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            white-space: nowrap !important;
            word-break: keep-all !important;
            line-height: 1.2;
            flex-shrink: 0;
            box-sizing: border-box;
        }
        .badge-account-status.active {
            background: rgba(16, 185, 129, 0.18);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.45);
            box-shadow: 0 0 10px rgba(16, 185, 129, 0.25);
        }
        .badge-account-status.paused {
            background: rgba(100, 116, 139, 0.2);
            color: #cbd5e1;
            border: 1px solid rgba(100, 116, 139, 0.35);
        }
        .status-pulse-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #34d399;
            display: inline-block;
            box-shadow: 0 0 6px #34d399;
            animation: pulseDot 2s infinite;
        }
        @keyframes pulseDot {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.85); }
        }
        .account-stats {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            background: rgba(0, 0, 0, 0.25);
            padding: 10px;
            border-radius: 10px;
            box-sizing: border-box;
            width: 100%;
        }
        .account-stat-box {
            min-width: 0;
            overflow: hidden;
        }
        .account-stat-num {
            font-size: 18px;
            font-weight: 800;
            line-height: 1.2;
        }
        .account-stat-lbl {
            font-size: 11px;
            color: var(--text-secondary);
            margin-top: 2px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .account-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 11px;
            color: var(--text-secondary);
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding-top: 10px;
            width: 100%;
            box-sizing: border-box;
        }
        .account-last-check {
            flex: 1 1 120px;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .account-actions {
            display: flex;
            gap: 6px;
            flex-shrink: 0;
        }
        .btn-account-action {
            padding: 4px 10px !important;
            font-size: 11px !important;
            min-height: 28px !important;
            line-height: 1 !important;
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
            border-radius: 14px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            backdrop-filter: blur(12px);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }
        th {
            background: #0b1120;
            padding: 12px 16px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }
        td {
            padding: 11px 16px;
            border-bottom: 1px solid var(--border);
            vertical-align: middle;
        }
        tr:hover td { background: rgba(255, 255, 255, 0.02); }
        tr.table-danger td { background: var(--critical-bg); }
        tr.table-warning td { background: var(--warning-bg); }

        .badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
            padding: 4px 10px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            white-space: nowrap !important;
            word-break: keep-all !important;
            overflow-wrap: normal !important;
            line-height: 1.3;
        }
        .badge-critical { background: var(--critical); color: #fff; box-shadow: 0 0 10px rgba(239, 68, 68, 0.4); }
        .badge-warning { background: var(--warning); color: #000; box-shadow: 0 0 10px rgba(245, 158, 11, 0.4); }
        .badge-success {
            background: rgba(16, 185, 129, 0.18);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.45);
            box-shadow: 0 0 10px rgba(16, 185, 129, 0.25);
        }
        .badge-pending {
            background: rgba(245, 158, 11, 0.18);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.45);
            box-shadow: 0 0 10px rgba(245, 158, 11, 0.2);
            animation: pulseSoft 2s infinite;
        }
        .badge-review {
            background: rgba(168, 85, 247, 0.18);
            color: #c084fc;
            border: 1px solid rgba(168, 85, 247, 0.45);
        }
        .badge-normal {
            background: rgba(100, 116, 139, 0.2);
            color: #cbd5e1;
            border: 1px solid rgba(100, 116, 139, 0.35);
        }
        .badge-high {
            background: rgba(239, 68, 68, 0.22);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.45);
            animation: pulse 2s infinite;
        }
        @keyframes pulseSoft {
            0%, 100% { opacity: 0.85; }
            50% { opacity: 1; }
        }

        .btn-view-content {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 6px 14px;
            font-size: 11.5px;
            font-weight: 700;
            color: #38bdf8;
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 8px;
            cursor: pointer;
            white-space: nowrap !important;
            text-decoration: none;
            transition: all 0.2s ease;
        }
        .btn-view-content:hover {
            background: rgba(56, 189, 248, 0.25);
            border-color: #38bdf8;
            color: #fff;
            box-shadow: 0 0 14px rgba(56, 189, 248, 0.4);
            transform: translateY(-1px);
        }

        .action-btn-group {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        .btn-delete-row {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 6px 10px;
            font-size: 13px;
            color: #f87171;
            background: rgba(239, 68, 68, 0.12);
            border: 1px solid rgba(239, 68, 68, 0.35);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-delete-row:hover {
            background: rgba(239, 68, 68, 0.28);
            border-color: #ef4444;
            color: #fff;
            box-shadow: 0 0 12px rgba(239, 68, 68, 0.4);
            transform: translateY(-1px);
        }

        /* Email Inspector Modal Dedicated Styles */
        .modal-email-inspector {
            max-width: 680px !important;
            width: 95% !important;
            max-height: 90vh !important;
            display: flex !important;
            flex-direction: column !important;
            padding: 0 !important;
            overflow: hidden !important;
            border: 1px solid rgba(255, 255, 255, 0.18) !important;
            border-radius: 16px !important;
            background: #0d1322 !important;
            box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.8) !important;
        }
        .modal-email-header {
            padding: 18px 24px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.02);
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
            flex-shrink: 0;
        }
        .modal-email-content {
            padding: 20px 24px;
            overflow-y: auto;
            flex: 1;
            -webkit-overflow-scrolling: touch;
        }
        .modal-email-footer {
            padding: 14px 24px;
            border-top: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.02);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
            flex-shrink: 0;
        }

        .email-body-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
            width: 100%;
            max-width: 100%;
            box-sizing: border-box;
        }
        .email-body-title {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11.5px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-secondary);
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            flex: 1;
        }
        .btn-copy-text {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
            padding: 6px 12px;
            font-size: 11px;
            font-weight: 700;
            color: #38bdf8;
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 8px;
            cursor: pointer;
            white-space: nowrap !important;
            word-break: keep-all !important;
            flex-shrink: 0 !important;
            line-height: 1;
            min-height: 30px;
            box-sizing: border-box;
            transition: all 0.2s ease;
        }
        .btn-copy-text:hover {
            background: rgba(56, 189, 248, 0.28);
            border-color: #38bdf8;
            color: #fff;
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.35);
        }
        .btn-copy-text:active {
            transform: scale(0.96);
        }
        #modalEmailBody {
            white-space: pre-wrap !important;
            word-break: break-word !important;
            overflow-wrap: anywhere !important;
            box-sizing: border-box !important;
            width: 100% !important;
            max-width: 100% !important;
        }

        .tag-vendor {
            background: #1e293b;
            border: 1px solid var(--border);
            padding: 2px 8px;
            border-radius: 5px;
            font-size: 12px;
        }
        .tag-invoice {
            font-family: 'JetBrains Mono', monospace;
            background: #090d16;
            border: 1px solid var(--border);
            padding: 2px 7px;
            border-radius: 5px;
            font-size: 11px;
        }
        .source-name {
            font-family: 'JetBrains Mono', monospace;
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
            border-radius: 16px;
            padding: 40px 24px;
            text-align: center;
            cursor: pointer;
            transition: all 0.25s;
            margin-bottom: 20px;
            backdrop-filter: blur(12px);
        }
        .dropzone-container:hover, .dropzone-container.drag-over {
            border-color: var(--accent);
            background: rgba(59, 130, 246, 0.08);
            box-shadow: 0 0 24px rgba(59, 130, 246, 0.2);
        }
        .dropzone-icon { font-size: 42px; margin-bottom: 12px; }
        .dropzone-text h3 { font-size: 16px; margin-bottom: 4px; }
        .dropzone-text p { color: var(--text-secondary); font-size: 12.5px; }
        .file-input { display: none; }

        /* Notification Toast */
        .toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: rgba(30, 41, 59, 0.95);
            backdrop-filter: blur(12px);
            color: #fff;
            border: 1px solid var(--border);
            border-left: 4px solid var(--accent);
            padding: 14px 20px;
            border-radius: 12px;
            box-shadow: 0 12px 30px rgba(0, 0, 0, 0.5);
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 12px;
            z-index: 9999;
            transform: translateY(100px);
            opacity: 0;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .toast.show { transform: translateY(0); opacity: 1; }
        .toast.success { border-left-color: var(--success); }
        .toast.error { border-left-color: var(--critical); }

        /* Modals */
        .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(10px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            padding: 16px;
        }
        .modal-overlay.active { display: flex; }
        .modal {
            background: #111a2e;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 18px;
            max-width: 480px;
            width: 100%;
            padding: 24px;
            box-shadow: 0 24px 48px rgba(0, 0, 0, 0.7);
            animation: modalPopIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes modalPopIn {
            from { opacity: 0; transform: scale(0.94) translateY(8px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
        }
        .modal h2 { font-size: 18px; margin-bottom: 12px; font-weight: 700; }
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
            padding: 9px 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg);
            color: var(--text-primary);
            font-size: 13px;
            outline: none;
            font-family: inherit;
        }
        .form-group input:focus, .form-group select:focus {
            border-color: var(--accent);
            box-shadow: 0 0 10px rgba(59, 130, 246, 0.35);
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

        /* Responsive Mobile Layout (Flawless Containment, Zero Horizontal Overflow) */
        @media (max-width: 768px) {
            body {
                padding: 12px 10px 60px 10px !important;
                width: 100% !important;
                max-width: 100% !important;
                overflow-x: hidden !important;
            }
            .navbar {
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
                overflow: hidden !important;
                flex-direction: column !important;
                align-items: center !important;
                text-align: center !important;
                gap: 14px !important;
                padding: 16px 12px !important;
                margin-bottom: 16px !important;
                border-radius: 16px !important;
            }
            .brand {
                flex-direction: column !important;
                align-items: center !important;
                text-align: center !important;
                gap: 8px !important;
                width: 100% !important;
                max-width: 100% !important;
            }
            .brand-icon {
                width: 48px !important;
                height: 48px !important;
                font-size: 24px !important;
                border-radius: 14px !important;
                box-shadow: 0 6px 20px rgba(59, 130, 246, 0.45) !important;
                flex-shrink: 0 !important;
            }
            .brand-text h1 {
                font-size: 18px !important;
                line-height: 1.3 !important;
                text-align: center !important;
                word-break: break-word !important;
            }
            .brand-text p {
                font-size: 11.5px !important;
                text-align: center !important;
                max-width: 100% !important;
                word-break: break-word !important;
                padding: 0 4px !important;
            }

            .nav-wrapper {
                width: 100% !important;
                max-width: 100% !important;
                display: flex !important;
                flex-direction: column !important;
                align-items: center !important;
                gap: 10px !important;
            }
            .monitor-pill {
                width: 100% !important;
                max-width: 340px !important;
                justify-content: center !important;
                text-align: center !important;
                padding: 8px 12px !important;
                font-size: 11.5px !important;
                box-sizing: border-box !important;
            }

            /* MATHEMATICALLY EXACT EQUAL BUTTONS ON MOBILE (3 rows of 2 buttons each) */
            .nav-actions {
                width: 100% !important;
                max-width: 440px !important;
                margin: 0 auto !important;
                display: grid !important;
                grid-template-columns: 1fr 1fr !important;
                gap: 8px !important;
                box-sizing: border-box !important;
            }
            .nav-actions .btn {
                width: 100% !important;
                min-width: 0 !important;
                height: 44px !important;
                min-height: 44px !important;
                max-height: 44px !important;
                box-sizing: border-box !important;
                padding: 0 6px !important;
                font-size: 11.5px !important;
                font-weight: 700 !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                text-align: center !important;
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                border-radius: 12px !important;
            }

            /* Health Ribbon on Mobile - Zero Overflow */
            .health-ribbon {
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
                overflow: hidden !important;
                padding: 10px 10px !important;
                margin-bottom: 16px !important;
            }
            .health-meta {
                flex-direction: column !important;
                align-items: center !important;
                text-align: center !important;
                gap: 6px !important;
                width: 100% !important;
                max-width: 100% !important;
            }
            .health-meta-left {
                display: flex !important;
                flex-wrap: wrap !important;
                align-items: center !important;
                justify-content: center !important;
                text-align: center !important;
                gap: 4px 6px !important;
                font-size: 10.5px !important;
                width: 100% !important;
                max-width: 100% !important;
                word-break: break-word !important;
            }
            .health-meta-right {
                font-size: 10px !important;
                padding: 2px 6px !important;
                max-width: 100% !important;
                word-break: break-word !important;
            }
            .health-bar-container {
                height: 6px !important;
                width: 100% !important;
            }
            .health-legend {
                display: flex !important;
                flex-wrap: wrap !important;
                justify-content: center !important;
                font-size: 10.5px !important;
                gap: 6px 10px !important;
                text-align: center !important;
                width: 100% !important;
                max-width: 100% !important;
            }

            /* Symmetrical 5-Card Stat Ribbon on Mobile */
            .stats-ribbon {
                grid-template-columns: 1fr 1fr !important;
                gap: 8px !important;
                width: 100% !important;
                max-width: 440px !important;
                margin: 0 auto 16px auto !important;
            }
            .stat-card {
                padding: 12px 8px !important;
                align-items: center !important;
                text-align: center !important;
                justify-content: center !important;
                min-width: 0 !important;
                overflow: hidden !important;
                box-sizing: border-box !important;
            }
            .stat-label {
                text-align: center !important;
                font-size: 9.5px !important;
                letter-spacing: 0 !important;
                word-break: break-word !important;
                line-height: 1.2 !important;
            }
            .stat-value {
                font-size: 20px !important;
                text-align: center !important;
                line-height: 1.2 !important;
            }
            .stat-desc {
                font-size: 9.5px !important;
                text-align: center !important;
                word-break: break-word !important;
                line-height: 1.2 !important;
            }
            .stats-ribbon .stat-card:last-child {
                grid-column: 1 / -1 !important;
                padding: 14px 10px !important;
                background: linear-gradient(135deg, rgba(15, 23, 42, 0.95) 30%, rgba(59, 130, 246, 0.22) 100%) !important;
                border-color: rgba(59, 130, 246, 0.45) !important;
                box-shadow: 0 4px 20px rgba(59, 130, 246, 0.22) !important;
            }

            /* Smooth Mobile Swipe Tabs */
            .tabs {
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
                display: flex !important;
                gap: 6px !important;
                overflow-x: auto !important;
                padding: 4px 2px 10px 2px !important;
                margin-bottom: 16px !important;
                scrollbar-width: none !important;
                -webkit-overflow-scrolling: touch !important;
                border-bottom: 1px solid var(--border) !important;
            }
            .tabs::-webkit-scrollbar {
                display: none !important;
            }
            .tab-btn {
                padding: 8px 12px !important;
                font-size: 11.5px !important;
                border-radius: 18px !important;
                background: var(--surface) !important;
                border: 1px solid var(--border) !important;
                white-space: nowrap !important;
                flex-shrink: 0 !important;
            }
            .tab-btn.active {
                background: linear-gradient(135deg, #3b82f6, #6366f1) !important;
                color: #fff !important;
                border-color: var(--accent) !important;
                box-shadow: 0 4px 14px rgba(59, 130, 246, 0.45) !important;
            }

            /* Mobile Toolbar and Search */
            .toolbar {
                flex-direction: column !important;
                align-items: stretch !important;
                gap: 10px !important;
                padding: 10px !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
                overflow: hidden !important;
            }
            .search-box {
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                box-sizing: border-box !important;
            }
            .search-box input {
                width: 100% !important;
                max-width: 100% !important;
                padding: 9px 12px !important;
                font-size: 12px !important;
                box-sizing: border-box !important;
            }
            .filters {
                width: 100% !important;
                max-width: 100% !important;
                display: flex !important;
                gap: 5px !important;
                overflow-x: auto !important;
                flex-wrap: nowrap !important;
                padding-bottom: 4px !important;
                scrollbar-width: none !important;
                -webkit-overflow-scrolling: touch !important;
                box-sizing: border-box !important;
            }
            .filters::-webkit-scrollbar {
                display: none !important;
            }
            .filter-btn {
                flex-shrink: 0 !important;
                white-space: nowrap !important;
                padding: 6px 10px !important;
                font-size: 10.5px !important;
            }

            /* Mobile Account Grid - 100% Contained & Stacked */
            .accounts-grid {
                grid-template-columns: 1fr !important;
                gap: 12px !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
            }
            .account-card {
                padding: 14px 12px !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
                overflow: hidden !important;
            }
            .account-header {
                flex-direction: row !important;
                justify-content: space-between !important;
                align-items: flex-start !important;
                gap: 8px !important;
                flex-wrap: wrap !important;
                width: 100% !important;
            }
            .account-info {
                flex: 1 1 150px !important;
                min-width: 0 !important;
                overflow: hidden !important;
            }
            .account-badge-wrap {
                margin-top: 2px !important;
                flex-shrink: 0 !important;
            }
            .badge-account-status {
                padding: 3px 8px !important;
                font-size: 10px !important;
            }
            .account-footer {
                flex-direction: column !important;
                align-items: flex-start !important;
                gap: 8px !important;
                width: 100% !important;
            }
            .account-last-check {
                width: 100% !important;
            }
            .account-actions {
                width: 100% !important;
                display: flex !important;
                gap: 8px !important;
            }
            .account-actions .btn {
                flex: 1 !important;
                justify-content: center !important;
                min-height: 38px !important;
            }

            /* Table to Card conversion - Strict Contained Rows */
            .table-responsive {
                width: 100% !important;
                max-width: 100% !important;
                background: transparent !important;
                border: none !important;
                box-sizing: border-box !important;
                overflow: hidden !important;
            }
            .table-responsive table,
            .table-responsive thead,
            .table-responsive tbody,
            .table-responsive tr,
            .table-responsive td {
                display: block !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
            }
            .table-responsive thead { display: none !important; }
            .table-responsive tr {
                background: var(--surface) !important;
                border: 1px solid var(--border) !important;
                border-radius: 14px !important;
                padding: 12px !important;
                margin-bottom: 12px !important;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25) !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
                overflow: hidden !important;
            }
            .table-responsive td {
                display: flex !important;
                justify-content: space-between !important;
                align-items: flex-start !important;
                padding: 6px 0 !important;
                border: none !important;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05) !important;
                font-size: 11.5px !important;
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                box-sizing: border-box !important;
                gap: 8px !important;
            }
            .table-responsive td:last-child { border-bottom: none !important; }
            .table-responsive td[colspan] {
                display: block !important;
                text-align: center !important;
                padding: 24px 10px !important;
            }
            .table-responsive td[colspan]::before {
                display: none !important;
            }
            .table-responsive td::before {
                content: attr(data-label);
                font-weight: 700 !important;
                font-size: 10.5px !important;
                color: var(--text-secondary) !important;
                text-transform: uppercase !important;
                flex-shrink: 0 !important;
                min-width: 70px !important;
                max-width: 90px !important;
                text-align: left !important;
                word-break: break-word !important;
                line-height: 1.4 !important;
            }
            .table-responsive td > * {
                min-width: 0 !important;
                max-width: calc(100% - 95px) !important;
                text-align: right !important;
                word-break: break-word !important;
                overflow-wrap: anywhere !important;
            }
            .table-responsive td[data-label="Action"] {
                display: flex !important;
                justify-content: stretch !important;
                align-items: center !important;
                padding-top: 10px !important;
                margin-top: 6px !important;
                border-top: 1px dashed rgba(255, 255, 255, 0.12) !important;
                border-bottom: none !important;
                width: 100% !important;
            }
            .table-responsive td[data-label="Action"]::before {
                display: none !important;
            }
            .table-responsive td[data-label="Action"] > * {
                max-width: 100% !important;
                width: 100% !important;
                text-align: center !important;
                justify-content: center !important;
            }
            .table-responsive td[data-label="Action"] .action-btn-group {
                display: flex !important;
                width: 100% !important;
                gap: 8px !important;
                max-width: 100% !important;
            }
            .table-responsive td[data-label="Action"] .btn-view-content {
                flex: 1 !important;
                width: auto !important;
                padding: 10px 14px !important;
                font-size: 13px !important;
                font-weight: 700 !important;
                letter-spacing: 0.3px !important;
                border-radius: 10px !important;
                background: linear-gradient(135deg, rgba(56, 189, 248, 0.22), rgba(59, 130, 246, 0.25)) !important;
                border: 1px solid rgba(56, 189, 248, 0.5) !important;
                color: #fff !important;
                box-shadow: 0 2px 10px rgba(56, 189, 248, 0.2) !important;
            }
            .table-responsive td[data-label="Action"] .btn-delete-row {
                flex-shrink: 0 !important;
                padding: 10px 16px !important;
                font-size: 15px !important;
                border-radius: 10px !important;
            }
            .table-responsive .source-name {
                max-width: 140px !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                white-space: nowrap !important;
                display: inline-block !important;
            }
            .table-responsive .badge {
                white-space: nowrap !important;
                word-break: keep-all !important;
                overflow-wrap: normal !important;
                display: inline-flex !important;
                align-items: center !important;
                justify-content: center !important;
                font-size: 10.5px !important;
                padding: 3px 8px !important;
                line-height: 1.3 !important;
            }

            /* Touch-Friendly Modals and Toast */
            .modal-overlay {
                padding: 10px !important;
            }
            .modal {
                padding: 20px 14px !important;
                margin: 0 !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
            }
            .modal-actions {
                flex-direction: column-reverse !important;
                gap: 8px !important;
            }
            .modal-actions .btn {
                width: 100% !important;
                justify-content: center !important;
                min-height: 44px !important;
            }
            .modal-email-inspector {
                width: 96% !important;
                max-height: 94vh !important;
                padding: 0 !important;
                margin: 0 auto !important;
                box-sizing: border-box !important;
                overflow-x: hidden !important;
            }
            .modal-email-header {
                padding: 14px 14px !important;
                box-sizing: border-box !important;
            }
            .modal-email-content {
                padding: 14px 12px !important;
                box-sizing: border-box !important;
                overflow-x: hidden !important;
            }
            .modal-email-footer {
                padding: 12px 14px !important;
                flex-direction: column !important;
                gap: 10px !important;
                box-sizing: border-box !important;
            }
            .modal-email-footer > div {
                width: 100% !important;
                display: flex !important;
                gap: 8px !important;
                box-sizing: border-box !important;
            }
            .modal-email-footer .btn {
                flex: 1 !important;
                justify-content: center !important;
            }
            .email-body-header {
                display: flex !important;
                flex-direction: row !important;
                justify-content: space-between !important;
                align-items: center !important;
                gap: 8px !important;
                width: 100% !important;
                max-width: 100% !important;
                box-sizing: border-box !important;
            }
            .email-body-title {
                font-size: 11px !important;
                flex: 1 !important;
                min-width: 0 !important;
                overflow: hidden !important;
                text-overflow: ellipsis !important;
                white-space: nowrap !important;
            }
            .btn-copy-text {
                padding: 6px 10px !important;
                font-size: 11px !important;
                flex-shrink: 0 !important;
                white-space: nowrap !important;
                word-break: keep-all !important;
                min-width: 82px !important;
                text-align: center !important;
                justify-content: center !important;
                box-sizing: border-box !important;
            }
            .toast {
                left: 10px !important;
                right: 10px !important;
                bottom: 12px !important;
                justify-content: center !important;
                text-align: center !important;
                font-size: 12px !important;
                padding: 10px 14px !important;
            }
        }

        /* Micro-Screen Extra Protection (<= 380px screens like iPhone SE / mini Androids) */
        @media (max-width: 380px) {
            body {
                padding: 8px 6px 50px 6px !important;
            }
            .navbar {
                padding: 12px 8px !important;
            }
            .brand-icon {
                width: 42px !important;
                height: 42px !important;
                font-size: 20px !important;
                border-radius: 12px !important;
                box-shadow: 0 4px 14px rgba(59, 130, 246, 0.45) !important;
                flex-shrink: 0 !important;
            }
            .brand-text h1 {
                font-size: 16px !important;
            }
            .brand-text p {
                font-size: 10.5px !important;
            }
            .nav-actions {
                gap: 6px !important;
            }
            .nav-actions .btn {
                height: 42px !important;
                min-height: 42px !important;
                max-height: 42px !important;
                font-size: 11px !important;
                padding: 0 4px !important;
            }
            .stats-ribbon {
                gap: 6px !important;
            }
            .stat-card {
                padding: 10px 6px !important;
            }
            .stat-label {
                font-size: 9px !important;
            }
            .stat-value {
                font-size: 18px !important;
            }
            .stat-desc {
                font-size: 9px !important;
            }
            .table-responsive tr {
                padding: 10px 8px !important;
            }
            .table-responsive td {
                font-size: 11px !important;
            }
            .table-responsive td::before {
                min-width: 65px !important;
                max-width: 80px !important;
                font-size: 10px !important;
            }
            .table-responsive td > * {
                max-width: calc(100% - 85px) !important;
            }
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
        <div class="nav-wrapper">
            <div class="monitor-pill" id="autoMonitorPill">
                <span class="pulse-container">
                    <span class="pulse-ring"></span>
                    <span class="pulse-dot"></span>
                </span>
                <span id="monitorStatusText">Auto-Monitor: Active</span>
            </div>
            <div class="nav-actions">
                <button class="btn btn-primary" id="btnRunLive" onclick="triggerPipeline(false)">
                    🚀 Scan Inboxes
                </button>
                <button class="btn btn-demo" id="btnRunDemo" onclick="triggerPipeline(true)">
                    🧪 Run Demo
                </button>
                <button class="btn btn-outline" id="btnLinkEmail" onclick="openLinkModal()">
                    ➕ Link Email
                </button>
                <button class="btn btn-outline" id="btnTestAlert" onclick="testDesktopAlert()">
                    🔔 Test Alert
                </button>
                <button class="btn btn-outline" id="btnUploadNav" onclick="setTab('upload')">
                    📤 Upload File
                </button>
                <button class="btn btn-danger" id="btnResetDemo" onclick="confirmResetDemo()">
                    🔄 Reset Demo
                </button>
            </div>
        </div>
    </header>

    <!-- Real-Time Pipeline Health Ribbon -->
    <div class="health-ribbon">
        <div class="health-meta">
            <div class="health-meta-left">
                <span class="health-pill-pulse"></span>
                <span><strong>Real-Time Pipeline Status</strong> &bull; Gmail API &bull; IMAP &bull; SQLite Live Sync</span>
            </div>
            <div class="health-meta-right" id="liveClockDisplay">
                ⚡ Synced at Live Stream
            </div>
        </div>
        <div class="health-bar-container">
            <div class="health-bar-segment seg-healthy" id="barHealthy" style="width: 82%;" title="Healthy Inventory"></div>
            <div class="health-bar-segment seg-warning" id="barWarning" style="width: 12%;" title="High-Value Warnings"></div>
            <div class="health-bar-segment seg-critical" id="barCritical" style="width: 6%;" title="Critical Low Stock"></div>
        </div>
        <div class="health-legend">
            <span><span class="legend-dot dot-healthy"></span> Healthy Stock: <strong id="legendHealthy">88%</strong></span>
            <span><span class="legend-dot dot-warning"></span> Price Warnings: <strong id="legendWarning">8%</strong></span>
            <span><span class="legend-dot dot-critical"></span> Critical Out/Low: <strong id="legendCritical">4%</strong></span>
        </div>
    </div>

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
                        <th style="text-align: center;">Action</th>
                    </tr>
                </thead>
                <tbody id="emailsTableBody">
                    <tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 30px;">Loading emails history...</td></tr>
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
    <div class="modal-overlay" id="resetModal" onclick="if(event.target===this) closeResetModal()">
        <div class="modal" style="max-width: 480px;">
            <h2 style="margin-top: 0; color: #f87171; display: flex; align-items: center; gap: 8px;">
                <span>🔄</span> Reset & Clean Slate
            </h2>
            <p style="color: var(--text-secondary); font-size: 13px; line-height: 1.5;">
                Clear simulated demo records or perform a complete wipe of all scanned email feed history so you can test fresh.
            </p>
            <div style="background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 12px; margin: 16px 0;">
                <label style="display: flex; align-items: flex-start; gap: 10px; cursor: pointer; font-size: 13px; color: #fff;">
                    <input type="checkbox" id="checkClearAllData" style="margin-top: 3px; accent-color: #ef4444; width: 16px; height: 16px;">
                    <span>
                        <strong>Complete Clean Slate (Wipe All Email Feed Records)</strong><br>
                        <span style="font-size: 11px; color: var(--text-secondary);">Clears all scanned emails, attachments, and inventory items so you have a completely fresh, empty dashboard to test new emails. (Monitored vendor settings stay saved).</span>
                    </span>
                </label>
            </div>
            <div class="modal-actions" style="display: flex; justify-content: flex-end; gap: 10px;">
                <button type="button" class="btn btn-outline" onclick="closeResetModal()">Cancel</button>
                <button type="button" class="btn btn-danger" onclick="executeResetDemo()">Reset Data Now</button>
            </div>
        </div>
    </div>

    <!-- MODAL: EMAIL CONTENT INSPECTOR -->
    <div class="modal-overlay" id="emailModal" onclick="if(event.target===this) closeEmailModal()">
        <div class="modal modal-email-inspector">
            <!-- Modal Header -->
            <div class="modal-email-header">
                <div style="min-width: 0; flex: 1;">
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap;">
                        <span style="font-size: 18px;">✉️</span>
                        <span id="modalEmailPriority" class="badge badge-normal">Normal</span>
                        <span id="modalEmailStatus" class="badge badge-success">PROCESSED</span>
                    </div>
                    <h3 id="modalEmailSubject" style="margin: 0; font-size: 16px; font-weight: 700; color: #fff; word-break: break-word; line-height: 1.4;">Email Subject</h3>
                </div>
                <button type="button" onclick="closeEmailModal()" style="background: none; border: none; color: var(--text-secondary); font-size: 26px; cursor: pointer; padding: 0 4px; line-height: 1; margin-top: -4px;">&times;</button>
            </div>

            <!-- Modal Scrollable Content -->
            <div class="modal-email-content">
                <!-- Sender Info Metadata Grid -->
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px; margin-bottom: 16px;">
                    <div>
                        <div style="font-size: 10.5px; text-transform: uppercase; color: var(--text-secondary); font-weight: 600; margin-bottom: 4px; letter-spacing: 0.5px;">From / Sender</div>
                        <div id="modalEmailSender" style="font-size: 12.5px; font-weight: 600; color: #fff; word-break: break-word;">-</div>
                    </div>
                    <div>
                        <div style="font-size: 10.5px; text-transform: uppercase; color: var(--text-secondary); font-weight: 600; margin-bottom: 4px; letter-spacing: 0.5px;">Received Time</div>
                        <div id="modalEmailReceived" style="font-size: 12.5px; color: var(--text-secondary);">-</div>
                    </div>
                    <div>
                        <div style="font-size: 10.5px; text-transform: uppercase; color: var(--text-secondary); font-weight: 600; margin-bottom: 4px; letter-spacing: 0.5px;">Attachments</div>
                        <div id="modalEmailFiles" style="font-size: 12.5px; font-weight: 700; color: #38bdf8;">-</div>
                    </div>
                </div>

                <!-- Email Message Body Box -->
                <div style="margin-bottom: 16px;">
                    <div class="email-body-header">
                        <div class="email-body-title">
                            <span>📝</span>
                            <span>Email Content</span>
                        </div>
                        <button type="button" id="btnCopyModalBody" onclick="copyModalEmailBody()" class="btn-copy-text" title="Copy email text">
                            📋 Copy Text
                        </button>
                    </div>
                    <div id="modalEmailBody" style="white-space: pre-wrap; word-break: break-word; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 13px; line-height: 1.6; color: #e2e8f0; background: rgba(0,0,0,0.3); padding: 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); max-height: 280px; overflow-y: auto;">
                        (No message text body)
                    </div>
                </div>

                <!-- Ingestion Notice -->
                <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 10px; padding: 12px; display: flex; align-items: center; gap: 10px;">
                    <div style="font-size: 22px; flex-shrink: 0;">📦</div>
                    <div style="font-size: 11.5px; color: #cbd5e1; line-height: 1.5;">
                        <strong style="color: #34d399;">Automated Ingestion Pipeline:</strong>
                        Product inventory records from this email are parsed, validated, and synced into your database and stock table.
                    </div>
                </div>
            </div>

            <!-- Modal Footer -->
            <div class="modal-email-footer">
                <div>
                    <button type="button" class="btn btn-danger" onclick="confirmDeleteCurrentEmail()" style="display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; font-size: 12px; background: rgba(239, 68, 68, 0.18); border: 1px solid rgba(239, 68, 68, 0.45); color: #f87171;">
                        🗑️ Delete Email Record
                    </button>
                </div>
                <div style="display: flex; gap: 10px;">
                    <button type="button" class="btn btn-outline" onclick="closeEmailModal()" style="padding: 8px 16px;">Close</button>
                    <button type="button" class="btn btn-primary" onclick="closeEmailModal(); setTab('inventory')">📦 View Inventory Table &rarr;</button>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL: DELETE EMAIL CONFIRMATION -->
    <div class="modal-overlay" id="deleteEmailModal" onclick="if(event.target===this) closeDeleteEmailModal()">
        <div class="modal" style="max-width: 440px;">
            <h2 style="color: #ef4444; display: flex; align-items: center; gap: 8px; margin-top: 0; font-size: 18px;">
                <span>🗑️</span> Delete Email Record?
            </h2>
            <p style="color: var(--text-secondary); font-size: 13px; line-height: 1.5; margin-bottom: 16px;">
                Are you sure you want to delete this email record from the system? This will remove it from your live email feed and audit log.
            </p>
            <div class="form-check" style="margin: 0 0 18px 0; background: rgba(255,255,255,0.03); padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.08); display: flex; align-items: center; gap: 8px;">
                <input type="checkbox" id="checkTrashInGmail" style="cursor: pointer; width: 16px; height: 16px;">
                <label for="checkTrashInGmail" style="cursor: pointer; font-size: 12px; color: #e2e8f0; margin: 0;">
                    Also move message to <strong>Gmail Trash</strong> (mail.google.com)
                </label>
            </div>
            <div class="modal-actions" style="display: flex; justify-content: flex-end; gap: 10px;">
                <button type="button" class="btn btn-outline" onclick="closeDeleteEmailModal()">Cancel</button>
                <button type="button" class="btn btn-danger" id="btnConfirmDeleteEmail" onclick="executeDeleteEmail()">Yes, Delete Record</button>
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

        // Test Notification Alert
        async function testDesktopAlert() {
            showToast("Testing notification alert...", "info");

            // 1. Browser Native Web Notification (works on Phone & Laptop browsers)
            if ("Notification" in window) {
                if (Notification.permission === "granted") {
                    try {
                        new Notification("Inventory & Email Automation", {
                            body: "🔔 Live Alert System is active & working properly!",
                            icon: "https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
                        });
                    } catch (e) {}
                } else if (Notification.permission !== "denied") {
                    Notification.requestPermission().then(perm => {
                        if (perm === "granted") {
                            try {
                                new Notification("Inventory & Email Automation", {
                                    body: "🔔 Live Alert System is active & working properly!",
                                    icon: "https://cdn-icons-png.flaticon.com/512/3135/3135715.png"
                                });
                            } catch (e) {}
                        }
                    });
                }
            }

            // 2. Dispatch to Server Backend API
            try {
                const res = await fetch('/api/test-desktop-alert', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    if (data.server_toast) {
                        showToast("Desktop alert sent! Check your Windows notification center.", "success");
                    } else {
                        showToast("Alert system active! Notification sent to your device browser.", "success");
                    }
                } else {
                    const errorMsg = data.error || data.message || "Unknown error";
                    showToast("Alert note: " + errorMsg, "error");
                }
            } catch (err) {
                showToast("Alert active on device (Server: " + err.message + ")", "info");
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

                // Dynamic calculation for the Real-Time Health Breakdown Ribbon
                const total = data.total_units_in_stock || 0;
                const critical = data.critical_alerts_count || 0;
                const warning = data.warning_alerts_count || 0;
                const totalAlerts = critical + warning;
                let pH = 88, pW = 8, pC = 4;
                if (total > 0) {
                    pC = Math.min(30, Math.max(3, Math.round((critical / Math.max(total, 50)) * 100) || 4));
                    pW = Math.min(30, Math.max(3, Math.round((warning / Math.max(total, 50)) * 100) || 8));
                    pH = Math.max(40, 100 - pC - pW);
                }
                const bH = document.getElementById('barHealthy');
                const bW = document.getElementById('barWarning');
                const bC = document.getElementById('barCritical');
                const lH = document.getElementById('legendHealthy');
                const lW = document.getElementById('legendWarning');
                const lC = document.getElementById('legendCritical');
                const clock = document.getElementById('liveClockDisplay');

                if (bH) bH.style.width = pH + '%';
                if (bW) bW.style.width = pW + '%';
                if (bC) bC.style.width = pC + '%';
                if (lH) lH.innerText = pH + '%';
                if (lW) lW.innerText = pW + '%';
                if (lC) lC.innerText = pC + '%';
                if (clock) clock.innerText = '⚡ Last Synced: ' + new Date().toLocaleTimeString();
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
                    ? `<span class="badge-account-status active"><span class="status-pulse-dot"></span> MONITORING ACTIVE</span>`
                    : `<span class="badge-account-status paused">⏸ PAUSED</span>`;

                return `
                <div class="account-card ${isActive ? 'active' : 'paused'}">
                    <div class="account-header">
                        <div class="account-info">
                            <div class="account-name" title="${escapeHtml(acc.account_name)}">${escapeHtml(acc.account_name)}</div>
                            <div class="account-email" title="${escapeHtml(acc.email_address)}">${escapeHtml(acc.email_address)}</div>
                        </div>
                        <div class="account-badge-wrap">${statusBadge}</div>
                    </div>
                    <div class="account-stats">
                        <div class="account-stat-box">
                            <div class="account-stat-num">${acc.total_emails_detected || 0}</div>
                            <div class="account-stat-lbl">Emails Inspected</div>
                        </div>
                        <div class="account-stat-box">
                            <div class="account-stat-num" style="color: var(--critical);">${acc.important_emails_detected || 0}</div>
                            <div class="account-stat-lbl">🚨 Important Alerts</div>
                        </div>
                    </div>
                    <div class="account-footer">
                        <span class="account-last-check">Last check: ${acc.last_checked_at || 'Pending scan'}</span>
                        <div class="account-actions">
                            <button class="btn btn-outline btn-account-action" onclick="toggleAccount(${acc.id}, ${!isActive})">
                                ${isActive ? '⏸️ Pause' : '▶️ Resume'}
                            </button>
                            <button class="btn btn-danger btn-account-action" onclick="deleteAccount(${acc.id})">
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
                    showToast("Failed to link email: " + (data.error || "Unknown error"), "error");
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
                tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 30px;">No incoming emails match filter.</td></tr>`;
                return;
            }

            tbody.innerHTML = filtered.map((em, idx) => {
                // Priority Badge
                let priClass = 'badge-normal';
                let priText = em.priority || 'Normal';
                if (priText.toLowerCase().includes('high')) {
                    priClass = 'badge-high';
                    priText = '🚨 HIGH PRIORITY';
                } else if (priText.toLowerCase().includes('review')) {
                    priClass = 'badge-review';
                    priText = '🔍 REVIEW';
                } else if (priText.toLowerCase().includes('promo')) {
                    priClass = 'badge-normal';
                    priText = '🏷️ PROMO';
                }
                const badgeHtml = `<span class="badge ${priClass}">${escapeHtml(priText)}</span>`;

                // Status Badge (Pending, Success, Failed)
                let statusClass = 'badge-normal';
                let statusText = em.status || 'PENDING';
                if (statusText === 'SUCCESS') {
                    statusClass = 'badge-success';
                    statusText = '✓ PROCESSED';
                } else if (statusText === 'PENDING') {
                    statusClass = 'badge-pending';
                    statusText = '⏳ PENDING';
                } else if (statusText === 'FAILED') {
                    statusClass = 'badge-critical';
                    statusText = '✕ FAILED';
                }
                const statusHtml = `<span class="badge ${statusClass}">${escapeHtml(statusText)}</span>`;

                const isHigh = (em.priority || '').toLowerCase().includes('high');
                const rowClass = isHigh ? 'email-high-priority' : '';

                return `
                <tr class="${rowClass}">
                    <td data-label="#"><strong>${idx + 1}</strong></td>
                    <td data-label="Sender">
                        <div style="min-width: 0; word-break: break-word;">
                            <div><strong>${escapeHtml(em.sender_name || 'Vendor')}</strong></div>
                            <div style="font-family: monospace; font-size: 11px; color: var(--text-secondary); word-break: break-all;">${escapeHtml(em.sender_email)}</div>
                        </div>
                    </td>
                    <td data-label="Subject">
                        <div style="min-width: 0; word-break: break-word;">
                            <div onclick="openEmailModal(${em.id})" style="cursor: pointer;" title="Click to view full email content">
                                <strong style="color: #38bdf8; text-decoration: underline; text-decoration-color: rgba(56,189,248,0.4);">${escapeHtml(em.subject)}</strong>
                            </div>
                            <div style="color: var(--text-secondary); font-size: 11px; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(em.body_preview || '')}</div>
                        </div>
                    </td>
                    <td data-label="Priority">${badgeHtml}</td>
                    <td data-label="Files" style="text-align: center; font-weight: 700;">${em.attachment_count || 0}</td>
                    <td data-label="Status">${statusHtml}</td>
                    <td data-label="Received" style="font-size: 11px; color: var(--text-secondary);">${em.received_at || ''}</td>
                    <td data-label="Action" style="text-align: center;">
                        <div class="action-btn-group">
                            <button type="button" class="btn-view-content" onclick="openEmailModal(${em.id})">👁️ View Content</button>
                            <button type="button" class="btn-delete-row" onclick="promptDeleteEmail(${em.id}, event)" title="Delete email record">🗑️</button>
                        </div>
                    </td>
                </tr>
                `;
            }).join('');
        }

        let emailToDeleteId = null;
        let currentModalEmailId = null;

        function openEmailModal(emailId) {
            currentModalEmailId = emailId;
            const em = emailsData.find(e => e.id === emailId);
            if (!em) return;

            document.getElementById('modalEmailSubject').textContent = em.subject || '(No Subject)';
            document.getElementById('modalEmailSender').innerHTML = `<strong>${escapeHtml(em.sender_name || 'Vendor')}</strong> <span style="font-family: monospace; font-size: 11px; color: var(--text-secondary); display: block;">&lt;${escapeHtml(em.sender_email)}&gt;</span>`;
            document.getElementById('modalEmailReceived').textContent = em.received_at || 'Just now';
            document.getElementById('modalEmailFiles').textContent = `${em.attachment_count || 0} file(s) attached`;

            const isHigh = (em.priority || '').toLowerCase().includes('high');
            const priBadge = document.getElementById('modalEmailPriority');
            priBadge.className = isHigh ? 'badge badge-high' : 'badge badge-normal';
            priBadge.textContent = em.priority || 'Normal';

            const statusBadge = document.getElementById('modalEmailStatus');
            if (em.status === 'SUCCESS') {
                statusBadge.className = 'badge badge-success';
                statusBadge.textContent = '✓ PROCESSED';
            } else if (em.status === 'PENDING') {
                statusBadge.className = 'badge badge-pending';
                statusBadge.textContent = '⏳ PENDING';
            } else {
                statusBadge.className = 'badge badge-normal';
                statusBadge.textContent = em.status || 'PENDING';
            }

            const bodyText = (em.body_preview && em.body_preview.trim()) ? em.body_preview.trim() : '(No plain text message body included in this email)';
            document.getElementById('modalEmailBody').textContent = bodyText;

            const modal = document.getElementById('emailModal');
            if (modal) modal.classList.add('active');
        }

        function closeEmailModal() {
            currentModalEmailId = null;
            const modal = document.getElementById('emailModal');
            if (modal) modal.classList.remove('active');
        }

        function confirmDeleteCurrentEmail() {
            if (!currentModalEmailId) return;
            promptDeleteEmail(currentModalEmailId);
        }

        function promptDeleteEmail(emailId, event) {
            if (event) event.stopPropagation();
            emailToDeleteId = emailId;
            const modal = document.getElementById('deleteEmailModal');
            if (modal) modal.classList.add('active');
        }

        function closeDeleteEmailModal() {
            emailToDeleteId = null;
            const modal = document.getElementById('deleteEmailModal');
            if (modal) modal.classList.remove('active');
        }

        async function executeDeleteEmail() {
            if (!emailToDeleteId) return;
            const checkTrash = document.getElementById('checkTrashInGmail');
            const deleteFromGmail = checkTrash ? checkTrash.checked : false;

            const btn = document.getElementById('btnConfirmDeleteEmail');
            btn.disabled = true;
            btn.textContent = 'Deleting...';

            try {
                const res = await fetch('/api/emails/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: emailToDeleteId, delete_from_gmail: deleteFromGmail }),
                });
                const data = await res.json();
                if (data.success) {
                    let msg = "Email record deleted successfully!";
                    if (data.trashed_in_gmail) {
                        msg += " (Moved to Gmail Trash)";
                    }
                    showToast(msg, "success");
                    closeDeleteEmailModal();
                    closeEmailModal();
                    await loadEmails();
                    await loadStatus();
                } else {
                    showToast("Failed to delete email: " + (data.error || "Unknown error"), "error");
                }
            } catch (err) {
                showToast("Error deleting email: " + err.message, "error");
            } finally {
                btn.disabled = false;
                btn.textContent = 'Yes, Delete Record';
            }
        }

        function copyModalEmailBody() {
            const bodyEl = document.getElementById('modalEmailBody');
            const text = bodyEl ? bodyEl.innerText : '';
            const btn = document.getElementById('btnCopyModalBody');
            if (!text || text === '(No message text body)') {
                showToast("No email text to copy", "info");
                return;
            }

            const onCopySuccess = () => {
                showToast("Email content copied to clipboard!", "success");
                if (btn) {
                    const orig = btn.innerHTML;
                    btn.innerHTML = '✅ Copied!';
                    btn.style.borderColor = '#34d399';
                    btn.style.color = '#34d399';
                    btn.style.background = 'rgba(52, 211, 153, 0.2)';
                    setTimeout(() => {
                        btn.innerHTML = orig;
                        btn.style.borderColor = '';
                        btn.style.color = '';
                        btn.style.background = '';
                    }, 2000);
                }
            };

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(onCopySuccess).catch(() => {
                    fallbackCopy(text, onCopySuccess);
                });
            } else {
                fallbackCopy(text, onCopySuccess);
            }
        }

        function fallbackCopy(text, onSuccess) {
            try {
                const ta = document.createElement("textarea");
                ta.value = text;
                ta.style.position = "fixed";
                ta.style.left = "-9999px";
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                const ok = document.execCommand('copy');
                document.body.removeChild(ta);
                if (ok && onSuccess) onSuccess();
                else showToast("Text copied to clipboard", "success");
            } catch (err) {
                showToast("Could not copy text", "error");
            }
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
            const checkClearAll = document.getElementById('checkClearAllData');
            const clearAll = checkClearAll ? checkClearAll.checked : false;
            closeResetModal();
            showToast(clearAll ? "Clearing all records & items..." : "Resetting demo data...", "info");
            try {
                const res = await fetch('/api/reset-demo', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ clear_all: clearAll })
                });
                const data = await res.json();
                if (data.success) {
                    showToast(data.message || (clearAll ? "All feed records wiped clean!" : "Demo data successfully reset!"), "success");
                    await loadStatus();
                    await loadAccounts();
                    await loadEmails();
                    await loadInventory();
                    await loadRejected();
                } else {
                    showToast("Failed to reset demo data: " + (data.error || "Unknown error"), "error");
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
        """Sends an HTML response with zero-cache headers for instant client updates."""
        response_bytes = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
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

    def do_HEAD(self) -> None:
        """Handles HTTP HEAD requests for uptime checkers and health monitors."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
        self.end_headers()

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

        if path == "/api/emails/delete":
            email_id = int(payload.get("id", 0))
            delete_from_gmail = bool(payload.get("delete_from_gmail", False))
            msg_id = db.delete_email_by_id(email_id)

            trashed_in_gmail = False
            if msg_id and delete_from_gmail:
                try:
                    from services.gmail_service import GmailService
                    gmail = GmailService()
                    if gmail.authenticate():
                        trashed_in_gmail = gmail.trash_message(msg_id)
                except Exception as exc:
                    logger.debug("Could not trash message in Gmail: %s", exc)

            self._send_json({"success": bool(msg_id), "trashed_in_gmail": trashed_in_gmail})
            return

        if path == "/api/test-desktop-alert":
            try:
                notifier = NotificationService(db_path=DATABASE_PATH)
                ok = notifier.send_desktop_notification(
                    sender_name="System Test Notification",
                    subject="Windows Desktop Alerts are active & working properly!",
                    priority="High Priority",
                )
                self._send_json({
                    "success": True,
                    "server_toast": ok,
                    "message": "Desktop alert triggered successfully." if ok else "Server running in cloud/headless mode; browser alert triggered."
                })
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
            clear_all = bool(payload.get("clear_all", False))
            try:
                if clear_all:
                    db.clear_all_data()
                    msg = "All email feed records and inventory items wiped clean."
                else:
                    db.reset_demo_data()
                    msg = "Demo data successfully reset."
                self._send_json({"success": True, "message": msg})
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
    for real linked monitored accounts while the web portal is running.
    Does not inject simulated demo data in the background.
    """
    time.sleep(5)  # Wait for server to stabilize
    while auto_monitor_state.is_running:
        try:
            from config.settings import GMAIL_CREDENTIALS_PATH
            if not GMAIL_CREDENTIALS_PATH.exists():
                auto_monitor_state.last_run_time = datetime.now()
                auto_monitor_state.last_status = "STANDBY"
                auto_monitor_state.last_summary = "Standby (Awaiting Gmail API credentials)"
            else:
                from main import run_pipeline
                # Runs pipeline to inspect all active monitored inboxes
                metrics = run_pipeline(demo_mode=False, open_dashboard=False)
                auto_monitor_state.last_run_time = datetime.now()
                auto_monitor_state.last_status = metrics.overall_status
                auto_monitor_state.last_summary = f"Scanned {metrics.emails_inspected} emails"
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
