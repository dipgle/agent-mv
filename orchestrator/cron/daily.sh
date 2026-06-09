#!/usr/bin/env bash
# Supervisor daily cron — runs at 02:00 local.
# Adds to crontab:
#   0 2 * * * cd /path/to/agent-mv && bash orchestrator/cron/daily.sh > logs/cron-daily.log 2>&1

set -euo pipefail
cd "$(dirname "$0")/../.."

# Activate venv (use Mac/Linux path; Windows uses .ps1 variant)
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

echo "=== $(date) — daily cron start ==="

# 1. System audit (bottleneck/regression/waste/reliability)
python orchestrator/supervisor/audit.py

# 2. Cost roll-up
python orchestrator/supervisor/cost_rollup.py

# 3. Fetch fresh outcomes for published videos (Tier 4 ground truth)
python orchestrator/supervisor/fetch_outcomes.py

# 4. Auto-promote any canaries that completed
python orchestrator/supervisor/auto_promote.py

echo "=== $(date) — daily cron done ==="
