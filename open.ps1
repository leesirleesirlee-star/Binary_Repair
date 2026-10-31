# Re-open the GUI with the current session token (token is written by start.ps1).
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$tokenFile = Join-Path $project "var\session.token"
if (-not (Test-Path $tokenFile)) {
    Write-Host "No session token found. Run .\start.ps1 first (it prints and stores the token)." -ForegroundColor Yellow
    exit 1
}
$token = (Get-Content $tokenFile -Raw).Trim()
Start-Process "http://127.0.0.1:8123/?token=$token"
Write-Host "Opened the GUI with the stored session token." -ForegroundColor Green
