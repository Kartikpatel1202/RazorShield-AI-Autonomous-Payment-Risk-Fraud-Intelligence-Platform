#!/usr/bin/env bash
# One-time local setup: environment file, backend virtualenv, frontend packages.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example - fill in the placeholder values."
fi

echo "==> Backend virtualenv"
python -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip
backend/.venv/bin/pip install -r backend/requirements-dev.txt

echo "==> Frontend packages"
(cd frontend && npm install)

echo "Setup complete."
