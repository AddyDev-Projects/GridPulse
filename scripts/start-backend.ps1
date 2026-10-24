<#
.SYNOPSIS
  Apply pending migrations, then start the GridPulse API (Windows).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1 -Port 8001
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPython)) { throw 'backend\.venv not found. Run scripts\setup.ps1 first.' }

Push-Location $Backend
try {
    Write-Host '==> Applying migrations (alembic upgrade head)' -ForegroundColor Cyan
    & $VenvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'alembic upgrade head failed. Is PostgreSQL running and .env correct?' }

    Write-Host "==> Starting API on http://${BindHost}:$Port (Ctrl+C to stop)" -ForegroundColor Cyan
    & $VenvPython -m uvicorn app.main:app --host $BindHost --port $Port
}
finally { Pop-Location }
