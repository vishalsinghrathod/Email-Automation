@echo off
title Smart Vendor Email & Inventory System - Portal
color 0b

echo =======================================================================
echo          SMART VENDOR EMAIL & INVENTORY - EMPLOYEE PORTAL
echo =======================================================================
echo   Launching interactive web portal in your default browser...
echo   Employees can trigger email checks, upload files, and view alerts.
echo   Developers can still use terminal commands (e.g. python main.py --demo)
echo =======================================================================
echo.

python main.py --ui

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] An error occurred while starting the web portal.
    pause
)

