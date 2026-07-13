@echo off
title NRS Traders System - Startup
color 0B
cls

echo.
echo  ============================================================
echo    NRS Traders Management System - Starting Up...
echo  ============================================================
echo.

:: Path to cloudflared (handles spaces in path safely via variable)
set CF="C:\Program Files (x86)\cloudflared\cloudflared.exe"
set CF_CONFIG="C:\Users\RONAN\.cloudflared\config.yml"

:: ---- Step 1: Start Django Production Server (Waitress) ----
echo  [1/2] Starting Django Production Server (Waitress)...
start "NRS Server - Django (Waitress)" cmd /k "cd /d "E:\NRS SOFTWARE" && .venv\Scripts\activate && python run_waitress.py"

:: Wait 3 seconds for the Django server to fully start
timeout /t 3 /nobreak >nul

:: ---- Step 2: Start Cloudflare Tunnel ----
echo  [2/2] Starting Cloudflare Tunnel (nrs.firebaseit.com)...
start "NRS Server - Cloudflare Tunnel" cmd /k "%CF% tunnel --config=%CF_CONFIG% run school-system"

:: Wait 3 seconds for tunnel to connect
timeout /t 3 /nobreak >nul

echo.
echo  ============================================================
echo    Your website is now LIVE at:
echo    https://nrs.firebaseit.com
echo  ============================================================
echo.
echo  Two windows have opened:
echo    - "NRS Server - Django (Waitress)"  ^<-- the web app
echo    - "NRS Server - Cloudflare Tunnel"  ^<-- the internet bridge
echo.
echo  DO NOT CLOSE those two windows while you want the site live.
echo  You can minimise them to the taskbar.
echo.
pause
