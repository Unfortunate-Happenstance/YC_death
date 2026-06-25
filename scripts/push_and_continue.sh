#!/usr/bin/env bash
# After priority scrape: merge, commit, push, start older batches.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run python scripts/merge_data.py

git add -A
if git diff --staged --quiet; then
  echo "No changes to commit."
else
  git commit -m "$(cat <<'EOF'
Update dashboard data

Refresh death scores and merge batch results into dashboard.json.
EOF
)"
fi

if git remote get-url origin &>/dev/null; then
  git push origin main
  echo "Pushed to GitHub Pages."
else
  echo "No git remote. Add one with:"
  echo "  git remote add origin git@github.com:YOUR_USER/YC_death.git"
  echo "  git push -u origin main"
  exit 1
fi

mkdir -p logs
if pgrep -f "scrape_death.py --older" >/dev/null; then
  echo "Older scrape already running."
else
  echo "Starting older batch scrape (2022–2024)..."
  nohup uv run python scripts/scrape_death.py --older >> logs/older-scrape.log 2>&1 &
  echo "PID: $! — tail -f logs/older-scrape.log"
fi
