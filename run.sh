#!/usr/bin/env bash
# caddie-ai — one-command launcher.
# First run sets everything up; later runs just start the server.
#   ./run.sh
set -e
cd "$(dirname "$0")"

# 1) virtualenv + dependencies (first run only)
if [ ! -d .venv ]; then
  echo "→ First run: creating virtualenv & installing dependencies…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  ./.venv/bin/python -m playwright install chromium
fi

# 2) API key check
if [ ! -f .env ] || ! grep -q "ANTHROPIC_API_KEY=sk-" .env 2>/dev/null; then
  echo "⚠  No Anthropic API key found."
  echo "   Create a .env file with:  ANTHROPIC_API_KEY=sk-ant-...    (https://console.anthropic.com)"
  echo "   The app runs without it, but AI scoring & drafting won't work."
fi

# 3) launch
echo "→ Starting caddie-ai at http://127.0.0.1:8000  (Ctrl+C to stop)"
(sleep 2 && (open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000 2>/dev/null)) &
exec ./.venv/bin/uvicorn ui.app:app --port 8000
