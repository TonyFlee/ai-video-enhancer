# Starts the FastAPI backend with live reload.
#   powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
Set-Location $backend

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Error "Virtual environment missing. Run:`n  cd '$backend'; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt"
}

if (-not (Test-Path ".\models\RIFE_fp32_timestep.onnx") -and -not (Test-Path ".\models\RIFE_fp32.onnx")) {
    Write-Host "RIFE model not found - downloading it now..." -ForegroundColor Yellow
    & ".\.venv\Scripts\python.exe" "scripts\download_models.py"
}

$port = if ($env:AVE_PORT) { $env:AVE_PORT } else { "8000" }
Write-Host "Backend listening on http://127.0.0.1:$port (Ctrl+C to stop)" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port --reload
