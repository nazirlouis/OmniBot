# OmniBot - run backend + Vite dashboard (Windows PowerShell)
# Run from the repository root after install:  .\scripts\start.ps1
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

$BackendRoot = Join-Path $RepoRoot "app\backend"
$Py = $null
foreach ($name in @(".venv", "venv")) {
    $candidate = Join-Path $BackendRoot "$name\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) {
        $Py = $candidate
        break
    }
}
if (-not $Py) {
    Write-Error "Backend venv not found (expected app\backend\.venv or app\backend\venv). Run .\scripts\install.ps1 first."
}

$DASHBOARD_URL = "http://127.0.0.1:5173"
$BACKEND_URL = "http://127.0.0.1:8000"

Write-Host ""
Write-Host "Starting OmniBot ..." -ForegroundColor Cyan
Write-Host ""

# Same console as Vite; logs may interleave. Stopped in finally when npm exits (Ctrl+C).
$backendProc = Start-Process -FilePath $Py -ArgumentList "app.py" -WorkingDirectory $BackendRoot -PassThru -NoNewWindow
if (-not $backendProc) {
    Write-Error "Failed to start backend."
}

Start-Sleep -Seconds 2

Push-Location (Join-Path $RepoRoot "app\frontend")
try {
    Write-Host "Backend process started (FastAPI on port 8000)."
    Write-Host ""
    Write-Host "Dashboard:  $DASHBOARD_URL" -ForegroundColor Green
    Write-Host "API:        $BACKEND_URL"
    Write-Host ""
    if ($env:OMNIBOT_NO_BROWSER) {
        Write-Host "Skipping browser (OMNIBOT_NO_BROWSER is set). Open the URL above manually."
    } else {
        Write-Host "Opening the dashboard in your browser shortly after Vite starts. Set OMNIBOT_NO_BROWSER=1 to skip."
        Write-Host "Press Ctrl+C here to stop the dev server; the backend will shut down too."
        Start-Job -ScriptBlock { Start-Sleep -Seconds 6; Start-Process "http://127.0.0.1:5173" } | Out-Null
    }
    Write-Host ""
    npm run dev
} finally {
    Pop-Location
    try { $backendProc.Refresh() } catch { }
    if ($backendProc -and -not $backendProc.HasExited) {
        Write-Host ""
        Write-Host "Stopping backend..." -ForegroundColor Gray
        Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue
    }
}
