# OmniBot - install dependencies (Windows PowerShell)
# Run from the repository root:  .\scripts\install.ps1
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host ""
Write-Host "OmniBot install" -ForegroundColor Cyan
Write-Host "---------------"
Write-Host ""

if (-not (Test-Command "python")) {
    Write-Error "Python is not on PATH. Install Python 3.10+ from https://www.python.org/downloads/ and re-run."
}
if (-not (Test-Command "node")) {
    Write-Error "Node.js is not on PATH. Install LTS from https://nodejs.org/ and re-run."
}

Push-Location (Join-Path $RepoRoot "app\backend")
try {
    $venvDir = $null
    if (Test-Path ".venv") {
        $venvDir = ".venv"
    } elseif (Test-Path "venv") {
        $venvDir = "venv"
        Write-Host "Using existing app\backend\venv"
    } else {
        Write-Host "Creating Python venv in app\backend\.venv ..."
        $created = $false
        foreach ($ver in @("3.12", "3.11", "3.13")) {
            if (Get-Command py -ErrorAction SilentlyContinue) {
                py "-$ver" -m venv .venv 2>$null
                if (Test-Path ".venv\Scripts\python.exe") { $created = $true; break }
            }
        }
        if (-not $created) {
            python -m venv .venv
        }
        if (-not (Test-Path ".venv\Scripts\python.exe")) {
            Write-Error "Could not create .venv. Install Python 3.12 from https://www.python.org/downloads/ (check 'Add to PATH') and re-run."
        }
        $venvDir = ".venv"
        $pv = & .\.venv\Scripts\python.exe -c "import sys; print('%d.%d' % (sys.version_info.major, sys.version_info.minor))"
        Write-Host "Created venv with Python $pv (Docker/CI use 3.12)."
    }
    $venvPy = Join-Path (Get-Location) "$venvDir\Scripts\python.exe"
    $venvVer = & $venvPy -c "import sys; print('%d.%d' % (sys.version_info.major, sys.version_info.minor))"
    Write-Host ""
    Write-Host "Hub venv: $venvDir (Python $venvVer) - pip installs into this interpreter, not whatever 'python' is first on PATH."
    Write-Host "Node: $(node -v | ForEach-Object { $_.Trim() })"
    Write-Host ""
    $pip = Join-Path (Get-Location) "$venvDir\Scripts\pip.exe"
    Write-Host "Installing Python dependencies (this may take a few minutes) ..."
    Write-Host "If pip prints 'Ignoring ... python_version >= 3.13', that is normal when this venv is 3.12 (markers for 3.13-only lines are skipped)."
    Write-Host ""
    & $pip install --upgrade pip
    & $pip install -r requirements.txt
} finally {
    Pop-Location
}

Push-Location (Join-Path $RepoRoot "app\frontend")
try {
    Write-Host "Installing frontend dependencies ..."
    if (Test-Path "package-lock.json") {
        npm ci
    } else {
        npm install
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "Next - start the hub and dashboard:" -ForegroundColor Yellow
Write-Host "  .\scripts\start.ps1"
Write-Host ""
Write-Host 'You do not need a .env file: paste your Gemini API key in the browser on first launch.'
Write-Host ""
