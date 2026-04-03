# OmniBot - pull latest Git changes and refresh dependencies (same as install)
# Run from the repository root:  .\scripts\update.ps1
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Test-Command($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

Write-Host ""
Write-Host "OmniBot update" -ForegroundColor Cyan
Write-Host "--------------"
Write-Host ""

if (-not (Test-Command "git")) {
    Write-Error "Git is not on PATH. Install Git from https://git-scm.com/downloads and re-run."
}

Push-Location $RepoRoot
try {
    if (-not (Test-Path ".git")) {
        Write-Error "Not a Git repository. Clone from GitHub or run this from the OmniBot repo root."
    }
    Write-Host "Pulling latest changes..." -ForegroundColor Gray
    git pull
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
& (Join-Path $PSScriptRoot "install.ps1")
