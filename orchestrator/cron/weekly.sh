#!/usr/bin/env bash
# Supervisor weekly cron — runs at 09:00 Mon local.
# Adds to crontab:
#   0 9 * * 1 cd /path/to/agent-mv && bash orchestrator/cron/weekly.sh > logs/cron-weekly.log 2>&1

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

echo "=== $(date) — weekly cron start ==="

# 1. External scan (HF + arxiv + pricing + ComfyUI nodes)
python orchestrator/supervisor/scan.py

# 2. Generate improvement proposals from scan findings
python orchestrator/supervisor/propose.py

# 3. Calibrate Tier 2 panel weights from outcome data
python orchestrator/supervisor/calibrate_panel.py

# 4. Calibrate hook signal weights from outcome data
python orchestrator/supervisor/calibrate_hook.py

# 5. Evolve champion / anti-pattern library
python orchestrator/supervisor/evolve_champions.py

# 6. Re-run auto-promotion (in case canaries finished over the weekend)
python orchestrator/supervisor/auto_promote.py

echo "=== $(date) — weekly cron done ==="
