#!/usr/bin/env bash
# One-shot recovery after the cloud computer is recreated.
# Restore .env first if the disk was wiped, then:
#   bash deploy/install.sh && bash deploy/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
bash deploy/install.sh
exec bash deploy/run.sh "$@"
