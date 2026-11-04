#!/usr/bin/env bash
# Aktualizacja na Pi: najnowszy kod z GitHuba i restart usługi.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
.venv/bin/pip install -q -e .
systemctl --user restart jarvis.service
systemctl --user --no-pager status jarvis.service | head -5
