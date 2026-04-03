#!/usr/bin/env bash
# OmniBot — pull latest Git changes and refresh dependencies (same as install)
# Run from the repository root:  chmod +x scripts/update.sh && ./scripts/update.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "OmniBot update"
echo "--------------"
echo ""

command -v git >/dev/null 2>&1 || { echo "Install Git and ensure git is on PATH." >&2; exit 1; }

cd "$REPO_ROOT"
if [[ ! -d .git ]]; then
  echo "Not a Git repository. Clone from GitHub or run this from the OmniBot repo root." >&2
  exit 1
fi

echo "Pulling latest changes..."
git pull

echo ""
bash "$SCRIPT_DIR/install.sh"
