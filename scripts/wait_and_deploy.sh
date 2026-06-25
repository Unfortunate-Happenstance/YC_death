#!/usr/bin/env bash
# Wait for priority scrape to finish, then push and start older batches.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Waiting for priority scrape to finish..."
while pgrep -f "scrape_death.py --priority" >/dev/null; do
  sleep 30
  tail -1 logs/priority-scrape.log 2>/dev/null || true
done

echo "Priority scrape done."
./scripts/push_and_continue.sh
