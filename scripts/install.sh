#!/usr/bin/env bash
# OmniBot — install dependencies (macOS / Linux)
# Run from the repository root:  chmod +x scripts/install.sh && ./scripts/install.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo ""
echo "OmniBot install"
echo "---------------"
echo ""

command -v python3 >/dev/null 2>&1 || { echo "Install Python 3.10+ and ensure python3 is on PATH."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Install Node.js LTS and ensure node/npm are on PATH."; exit 1; }

echo "Found $(python3 --version) and $(node -v)"

cd "$REPO_ROOT/app/backend"
if [[ ! -d .venv ]]; then
  echo "Creating Python venv in app/backend/.venv ..."
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
echo "Installing Python dependencies (this may take a few minutes) ..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
deactivate

cd "$REPO_ROOT/app/frontend"
echo "Installing frontend dependencies ..."
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi

echo ""
echo "Done."
echo ""
echo "Next — start the hub and dashboard:"
echo "  ./scripts/start.sh"
echo ""
echo "You do not need a .env file: paste your Gemini API key in the browser on first launch."
echo ""
