#!/usr/bin/env bash
# Aktualizacja na Pi: najnowszy kod z GitHuba, zależności i restart usługi (jeśli działa).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
.venv/bin/pip install -q -e .
if systemctl --user is-active --quiet jarvis.service; then
  systemctl --user restart jarvis.service
  systemctl --user --no-pager status jarvis.service | head -5
else
  echo "Usługa jarvis nie działa, nie uruchamiam jej (start: systemctl --user enable --now jarvis)."
fi
