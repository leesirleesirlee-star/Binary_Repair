@echo off
rem Re-open the GUI with the stored session token.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0open.ps1"
pause
