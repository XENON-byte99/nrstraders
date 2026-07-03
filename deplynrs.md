# NRS Software Deployment Guide

This document outlines the correct procedure for deploying the NRS Software project to the VPS. Following these exact steps avoids the issues with incorrect folders and services that caused deployment failures in the past.

## VPS Details
- **IP Address:** `194.238.19.210`
- **Username:** `root`
- **Password:** `qscfkz0B&VY?rf7D`
- **Web Root Directory:** `/var/www/nrs-software`
- **Virtual Environment:** `/var/www/nrs-software/venv`
- **Systemd Service:** `nrs-software.service`
- **Domain:** `nrs.firebaseit.com`

---

## Deployment Steps

### 1. Package the Code Locally
First, create a zip archive containing only the necessary files and folders from your local machine.

```powershell
tar.exe -a -c -f deploy.zip accounts audit documents nrs_project static templates manage.py requirements.txt db.sqlite3
```

### 2. Upload to the Server
Upload `deploy.zip` to the VPS (e.g., placing it in `/tmp/deploy.zip`) using SFTP or a deployment script.

### 3. Extract to the Correct Directory
SSH into the VPS and extract the zip file directly into `/var/www/nrs-software`.

```bash
unzip -o /tmp/deploy.zip -d /var/www/nrs-software
```

### 4. Fix Permissions
Ensure the web server (`www-data`) owns the files and has permission to write to the database.

```bash
chown -R www-data:www-data /var/www/nrs-software
chmod 666 /var/www/nrs-software/db.sqlite3
```

### 5. Install Dependencies and Run Django Commands
**CRITICAL:** Always use `venv/bin/python` (not `.venv`) when running commands on the VPS.

```bash
cd /var/www/nrs-software
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py collectstatic --no-input
venv/bin/python manage.py migrate
```

### 6. Restart the Service
**CRITICAL:** The service is named `nrs-software`, not `nrs`. If this is not restarted, the live site will continue serving old cached code and database connections.

```bash
systemctl restart nrs-software
```

---

## Known Pitfalls to Avoid
- **Wrong Directory:** Do NOT deploy to `/var/www/nrs`. Nginx is configured to serve from `/var/www/nrs-software`.
- **Wrong Virtual Environment:** Do NOT create or use `.venv` on the server. The server expects the environment to be named `venv`.
- **Forgetting to Restart:** Gunicorn caches Python code and database handles in memory. Always run `systemctl restart nrs-software` after uploading new files.
