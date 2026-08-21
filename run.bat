@echo off
title NSE Option Chain Dashboard
echo ============================================
echo   NSE Option Chain Dashboard - COA 1.0
echo ============================================
echo.
echo Starting server on http://127.0.0.1:8765
echo Press Ctrl+C to stop.
echo.

cd /d "%~dp0"
python -m backend.main

pause
