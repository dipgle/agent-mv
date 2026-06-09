#!/usr/bin/env bash
# Convenience entry point for macOS/Linux.
#   ./run.sh setup            -> run infra/setup.sh
#   ./run.sh check            -> scripts/check_env.py
#   ./run.sh pipeline ARGS    -> orchestrator/pipeline.py ARGS
#   ./run.sh audit            -> orchestrator/supervisor/audit.py
#   ./run.sh cost             -> orchestrator/supervisor/cost_rollup.py
#   ./run.sh scan             -> orchestrator/supervisor/scan.py
#   ./run.sh propose          -> orchestrator/supervisor/propose.py
#   ./run.sh promote          -> orchestrator/supervisor/auto_promote.py
#   ./run.sh cron-daily       -> orchestrator/cron/daily.sh
#   ./run.sh cron-weekly      -> orchestrator/cron/weekly.sh
#   ./run.sh dashboard        -> open eval/dashboard.html

set -euo pipefail
cd "$(dirname "$0")"

if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

cmd="${1:-}"
shift || true

case "$cmd" in
    setup)       bash infra/setup.sh ;;
    check)       python scripts/check_env.py "$@" ;;
    pipeline)    python orchestrator/pipeline.py "$@" ;;
    audit)       python orchestrator/supervisor/audit.py "$@" ;;
    cost)        python orchestrator/supervisor/cost_rollup.py "$@" ;;
    scan)        python orchestrator/supervisor/scan.py "$@" ;;
    propose)     python orchestrator/supervisor/propose.py "$@" ;;
    promote)     python orchestrator/supervisor/auto_promote.py "$@" ;;
    cron-daily)  bash orchestrator/cron/daily.sh ;;
    cron-weekly) bash orchestrator/cron/weekly.sh ;;
    dashboard)
        if command -v open >/dev/null; then open eval/dashboard.html
        elif command -v xdg-open >/dev/null; then xdg-open eval/dashboard.html
        else echo "Open eval/dashboard.html in your browser"; fi ;;
    *)
        echo "Usage: ./run.sh <command> [args]"
        echo ""
        echo "Commands:"
        echo "  setup        Install everything (first run)"
        echo "  check        Verify environment"
        echo "  pipeline     Render a video"
        echo "  audit        System audit (bottleneck/waste/...)"
        echo "  cost         Cost roll-up"
        echo "  scan         External scan"
        echo "  propose      Generate improvement proposals"
        echo "  promote      Run auto-promotion canaries"
        echo "  cron-daily   Run daily cron now"
        echo "  cron-weekly  Run weekly cron now"
        echo "  dashboard    Open eval dashboard"
        ;;
esac
