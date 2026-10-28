#!/usr/bin/env bash
# Create a virtualenv, install the bridge, and prepare runtime directories.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install .

mkdir -p outbox data
chmod 700 outbox data

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Wrote .env from .env.example. Fill it in before starting."
else
  chmod 600 .env
fi

echo "Installed. Start with: bash deploy/run.sh"
