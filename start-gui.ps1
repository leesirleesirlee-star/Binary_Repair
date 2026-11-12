# BinaryRepair GUI launcher (Windows PowerShell).
# Creates .venv, installs requirements, generates a local session token, serves
# http://127.0.0.1:8123/?token=... and opens the browser.
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $project

$python = "py"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $python = "python" }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..." -ForegroundColor Cyan
    & $python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
}

$var = Join-Path $project "var"
New-Item -ItemType Directory -Force $var | Out-Null
$tokenFile = Join-Path $var "session.token"
$token = ""
if (Test-Path $tokenFile) { $token = (Get-Content $tokenFile -Raw).Trim() }
if (-not $token) {
    $token = (& ".venv\Scripts\python.exe" -c "import secrets;print(secrets.token_urlsafe(32))").Trim()
    Set-Content -Path $tokenFile -Value $token -NoNewline
}
$env:BINARYREPAIR_SESSION_TOKEN = $token
$port = if ($env:BINARYREPAIR_PORT) { $env:BINARYREPAIR_PORT } else { "8123" }
$url = "http://127.0.0.1:$port/?token=$token"

Write-Host ""
Write-Host " BinaryRepair Workbench" -ForegroundColor Cyan
Write-Host " Open: $url" -ForegroundColor Green
Write-Host " Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

Start-Job -ScriptBlock {
    param($target)
    Start-Sleep -Seconds 2
    Start-Process $target
} -ArgumentList $url | Out-Null

& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port
