rem Start the canonical controller API (app.controller_api:app) on 127.0.0.1:8000.

@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-controller.ps1"
pause
