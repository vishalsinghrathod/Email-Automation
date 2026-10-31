"""
Shortcut Launcher for Employee Web Portal
Usage: python app.py
"""
import sys
from services.web_service import start_web_server

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
    start_web_server(port=port, open_browser=True)

