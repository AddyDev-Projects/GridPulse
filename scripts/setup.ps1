<#
.SYNOPSIS
  One-time GridPulse setup for Windows PowerShell 5.1+ / PowerShell 7+.

.DESCRIPTION
  1. Creates .env from .env.example if missing (then stops so you can fill it in).
  2. Creates backend\.venv with Python 3.12+ and installs backend\requirements.txt.
  3. Creates the PostgreSQL role and databases named in .env (backend\scripts\db_setup.py).
     Nothing is ever dropped or reset.
  4. Applies Alembic migrations to the development database.
  5. Installs frontend dependencies with npm ci.

  PostgreSQL itself must already be running (Docker or native install, see README).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -SkipDatabase -SkipFrontend
#>
[CmdletBinding()]
param(
    [switch]$SkipDatabase,
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$VenvPython = Join-Path $Backend '.venv\Scripts\python.exe'

function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Assert-Exit([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

Push-Location $Root
try {
    if (-not (Test-Path (Join-Path $Root '.env'))) {
        Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
        Write-Host 'Created .env from .env.example.' -ForegroundColor Yellow
        Write-Host 'Edit .env (database passwords and GRIDPULSE_PG_ADMIN_* settings), then run this script again.' -ForegroundColor Yellow
        exit 1
    }

    Step 'Checking Python 3.12+'
    if (Get-Command py -ErrorAction SilentlyContinue) { $PyExe = 'py'; $PyArgs = @('-3.12') }
    else { $PyExe = 'python'; $PyArgs = @() }
    & $PyExe @PyArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.12 or newer is required. Install it from python.org (enable 'Add python.exe to PATH') and run again."
    }

    if (-not (Test-Path $VenvPython)) {
        Step 'Creating backend\.venv'
        & $PyExe @PyArgs -m venv (Join-Path $Backend '.venv')
        Assert-Exit 'Creating the virtual environment'
    }

    Step 'Installing backend dependencies'
    & $VenvPython -m pip install --upgrade pip
    Assert-Exit 'Upgrading pip'
    & $VenvPython -m pip install -r (Join-Path $Backend 'requirements.txt')
    Assert-Exit 'Installing backend\requirements.txt'

    Push-Location $Backend
    try {
        if (-not $SkipDatabase) {
            Step 'Creating PostgreSQL role and databases (never drops anything)'
            & $VenvPython scripts\db_setup.py
            Assert-Exit 'Database setup'
        }
        Step 'Applying migrations to the development database'
        & $VenvPython -m alembic upgrade head
        Assert-Exit 'alembic upgrade head'
    }
    finally { Pop-Location }

    if (-not $SkipFrontend) {
        Step 'Checking Node.js (needs 20.19+ or 22.12+)'
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is not installed or not on PATH.' }
        node -e "const [a,b]=process.versions.node.split('.').map(Number); process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>22?0:1)"
        if ($LASTEXITCODE -ne 0) { throw "Node.js $(node --version) is too old. Install Node 22 LTS (or 20.19+)." }

        Step 'Installing frontend dependencies'
        Push-Location (Join-Path $Root 'frontend')
        try {
            npm ci
            Assert-Exit 'npm ci'
        }
        finally { Pop-Location }
    }

    Write-Host ''
    Write-Host 'Setup complete.' -ForegroundColor Green
    Write-Host '  Backend:  powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1'
    Write-Host '  Frontend: cd frontend; npm run dev   (then open http://localhost:5173)'
}
finally { Pop-Location }
