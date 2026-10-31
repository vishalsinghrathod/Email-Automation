# 🚀 Deployment Guide: Smart Vendor Email & Inventory Automation System

This guide explains how to deploy this project across three enterprise-standard environments:
1. **Docker Container Deployment** *(Recommended for cloud & interviews)*
2. **Local Office PC / Windows Server** *(Best for internal office LAN without cloud bills)*
3. **Cloud VPS (AWS EC2 / DigitalOcean / Ubuntu)** *(24/7 cloud hosting with domain & SSL)*

---

## Method 1: Docker Container Deployment (Industry Standard)

Using Docker packages the Python 3.11 environment, SQLite database, dependencies, and web application into an isolated container that runs anywhere.

### Prerequisites:
- Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or Docker Engine (Linux).

### Step-by-Step Commands:

#### 1. Build & Start the Container:
```bash
docker compose up -d --build
```

#### 2. Verify Container Health:
```bash
docker ps
```
You will see `smart-vendor-automation` running on port `0.0.0.0:8080` with status `healthy`.

#### 3. Access Web Portal:
Open your browser and navigate to:
```text
http://localhost:8080
```

#### 4. Useful Docker Commands:
```bash
# View live application logs
docker compose logs -f

# Stop the container
docker compose down

# Restart the container
docker compose restart
```

> **Data Persistence:** The `docker-compose.yml` mounts `./data` and `./reports` to your host computer. Your SQLite database, stock records, and downloaded vendor attachments will **never** be lost even if the container is rebuilt or stopped.

---

## Method 2: Local Office PC / Windows Server (Zero Cloud Cost)

If your company operates on an office local network (LAN) and wants operations employees to access the portal:

### Steps:

#### 1. Start the Web Portal on Port 8080:
```powershell
python main.py --ui --port 8080
```
*(Or double-click `start_app.bat`)*

#### 2. Find the Host PC's Local IP Address:
In PowerShell, run:
```powershell
ipconfig
```
Look for **IPv4 Address** (for example: `192.168.1.45`).

#### 3. Allow Port 8080 in Windows Firewall (One-Time Setup):
Run this single command in PowerShell as Administrator:
```powershell
New-NetFirewallRule -DisplayName "Vendor Automation Portal" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
```

#### 4. Access from any Office Computer:
Any employee on the same office Wi-Fi/LAN can now open their browser and visit:
```text
http://192.168.1.45:8080
```

#### 5. Auto-Start on Windows Boot (Optional):
You can place a shortcut of `start_app.bat` into the Windows Startup folder:
* Press `Win + R`, type `shell:startup`, and press Enter.
* Paste a shortcut to `start_app.bat`.
* The system will automatically start whenever the computer turns on!

---

## Method 3: Cloud VPS (AWS EC2 / DigitalOcean Ubuntu Linux)

For 24/7 high-availability cloud access from anywhere in the world.

### Architecture Stack:
* **Server:** AWS EC2 t3.small / DigitalOcean Ubuntu 22.04 LTS
* **Service Manager:** `systemd` (Restarts application automatically if it crashes)
* **Reverse Proxy:** `Nginx` (Handles HTTPS and port forwarding)
* **SSL Certificate:** `Certbot` (Free Let's Encrypt SSL/HTTPS)

### Step-by-Step Setup:

#### 1. Clone & Install Dependencies on Server:
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv nginx certbot python3-certbot-nginx git
git clone <your-repo-url> /opt/vendor-automation
cd /opt/vendor-automation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 2. Create Systemd Service (Auto-Restart Daemon):
Create a service file at `/etc/systemd/system/vendor-automation.service`:
```ini
[Unit]
Description=Smart Vendor Email & Inventory Automation
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/vendor-automation
ExecStart=/opt/vendor-automation/venv/bin/python main.py --ui --port 8080 --no-browser
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable vendor-automation
sudo systemctl start vendor-automation
```

#### 3. Configure Nginx Reverse Proxy:
Create `/etc/nginx/sites-available/vendor-automation`:
```nginx
server {
    listen 80;
    server_name inventory.yourcompany.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```
Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/vendor-automation /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### 4. Enable Free HTTPS (SSL):
```bash
sudo certbot --nginx -d inventory.yourcompany.com
```

Your system is now live at `https://inventory.yourcompany.com`!

---

## Summary of Tools Used

| Purpose | Tool Name | Why It Was Chosen |
|---|---|---|
| **Containerization** | Docker & Docker Compose | Single-command deployment, isolates environment, persistent volumes |
| **Reverse Proxy** | Nginx | High-performance HTTP traffic routing, SSL termination |
| **Process Manager** | Systemd (Linux) / Windows Service | Ensures app runs 24/7 and auto-restarts on server reboot |
| **Database** | SQLite with WAL mode | Fast, embedded, zero database server maintenance |
| **SSL / Security** | Let's Encrypt / Certbot | Automated, free HTTPS certificates |
| **Email Protocol** | Google OAuth 2.0 / Gmail API | Secure token-based access without sharing email passwords |

