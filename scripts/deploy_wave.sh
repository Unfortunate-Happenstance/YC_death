#!/usr/bin/env bash
# Run priority scrape, commit, push to GitHub Pages, then scrape older batches.
set -euo pipefail
cd "$(dirname "$0")/.."

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

echo "=== Priority scrape (latest → 2025) ==="
uv run python scripts/scrape_death.py --priority 2>&1 | tee "$LOG_DIR/priority-scrape.log"

echo "=== Merging dashboard ==="
uv run python scripts/merge_data.py

echo "=== Committing & pushing ==="
git add -A
git commit -m "$(cat <<'EOF'
Deploy priority batch data (2025–latest)

Death scores scraped for Winter 2025 through Winter 2027 batches.
EOF
)" || echo "Nothing new to commit"
git push origin main

echo "=== Starting older batch scrape (2022–2024) in background ==="
nohup uv run python scripts/scrape_death.py --older >> "$LOG_DIR/older-scrape.log" 2>&1 &
echo "Older scrape PID: $! — tail -f $LOG_DIR/older-scrape.log"
