# Starts the Next.js frontend (proxies /api to the backend).
#   powershell -ExecutionPolicy Bypass -File scripts\start-frontend.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root "frontend"
Set-Location $frontend

if (-not (Test-Path ".\node_modules")) {
    Write-Host "Installing npm dependencies..." -ForegroundColor Yellow
    npm install
}

Write-Host "Frontend on http://localhost:3000 (Ctrl+C to stop)" -ForegroundColor Cyan
npm run dev
