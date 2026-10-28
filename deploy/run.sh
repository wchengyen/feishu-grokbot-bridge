#!/usr/bin/env bash
# Load nothing from the shell. The process reads .env itself and does not print it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "Run bash deploy/install.sh first." >&2
  exit 1
fi

exec .venv/bin/python -m bridge.main "$@"
