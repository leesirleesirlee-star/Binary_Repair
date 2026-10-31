@echo off
rem One-click launcher for cmd.exe; delegates to start.ps1 (session token + URL).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
