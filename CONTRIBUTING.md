# Contributing to agent-mv

Thank you for interest in contributing! This document outlines the development workflow, testing requirements, and style conventions.

## Development Setup

### Prerequisites
- Python 3.10+
- Git
- ffmpeg (binary on PATH)
- For GPU acceleration: NVIDIA CUDA 12.1+ or Apple Metal

### Clone and Bootstrap

```bash
# Clone the repository
git clone https://github.com/dipgle/agent-mv.git
cd agent-mv

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Verify environment
python orchestrator/_console.py check --verbose
```

### OS-Specific Setup

Follow the relevant installation guide:
- **Windows 10/11**: [docs/INSTALL-WIN.md](docs/INSTALL-WIN.md)
- **macOS M-series**: [docs/INSTALL-MAC.md](docs/INSTALL-MAC.md)
- **Linux (Ubuntu/Debian)**: [docs/INSTALL-LINUX.md](docs/INSTALL-LINUX.md)

Each guide includes optional GPU setup and model weight downloads.

## Testing

### Unit + Integration Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test module
pytest tests/test_cost_gate.py

# Run with coverage
pytest --cov=orchestrator --cov=eval
```

### Syntax Checks

```bash
# Lint with ruff
ruff check orchestrator/ scripts/ eval/

# Format code (optional, ruff can auto-fix)
ruff format orchestrator/ scripts/ eval/

# Check all Python files parse without syntax errors
python -c "
import ast
from pathlib import Path
for p in Path('.').rglob('*.py'):
    ast.parse(open(p).read())
print('All Python files OK')
"
```

### Shell Script Checks

```bash
# Bash syntax check
for file in orchestrator/cron/*.sh infra/*.sh scripts/*.sh run.sh; do
  bash -n "$file"
done

# ShellCheck lint (warnings only, non-fatal)
shellcheck orchestrator/cron/*.sh infra/*.sh scripts/*.sh run.sh || true
```

### Smoke Test

Before declaring a feature complete, run a minimal render to verify the pipeline:

```bash
python orchestrator/pipeline.py \
    --intent "Test 5s clip blue sky" \
    --feature-id SMOKE-001 \
    --aspect 16:9 \
    --duration 5
```

Expected output: `out/SMOKE-001/final.mp4` exists (quality is secondary for smoke).

## Code Style

### English Comments

All code comments must be in English, even though the codebase supports Vietnamese UI/docs.

```python
# Good: English comment
# Transform the prompt via cascade routing
cost, response = cascade_route(prompt)

# Bad: Vietnamese comment
# Biến đổi prompt qua cascade routing
cost, response = cascade_route(prompt)
```

### Ruff Configuration

The project uses `ruff` for linting. Key rules:
- Line length: 100 characters (default)
- E501 (line too long): enforced
- F401 (unused imports): enforced
- No auto-fixes applied in CI; lint failures block merge

### SQL Conventions

- SQL schema in `eval/schema.sql`
- Always uppercase keywords: `SELECT`, `WHERE`, `JOIN`, `GROUP BY`
- Lowercase table/column names
- VIEWs prefixed with `v_` (e.g., `v_model_runs`)

### Shell Script Conventions

- Use `bash` for portability (not `sh`)
- Quote all variables: `"$VAR"` not `$VAR`
- Check exit codes: `command || exit 1`
- Use `set -e` at top to fail on first error

### Python Conventions

- Type hints where helpful (not required but encouraged)
- Docstrings for public functions
- Log events via `log_event()` devlog calls (see `orchestrator/lib/devlog.py`)
- No hardcoded paths; use `pathlib.Path` for cross-OS

## Pull Request Guidelines

### Before Opening a PR

1. **Write a failing test first** (TDD discipline)
   - Add test case to `tests/` or directly to test role script
   - Run with `pytest` — should fail before your code change

2. **Implement minimum code** to make the test pass
   - Avoid speculative features
   - Refactor only after test is green

3. **Verify all checks pass**
   ```bash
   ruff check orchestrator/ scripts/ eval/
   pytest
   shellcheck orchestrator/cron/*.sh
   ```

4. **Update docs** if behavior changes
   - Update relevant `.md` file in `docs/`
   - Add note to `CHANGELOG.md` under `[Unreleased]`

### PR Template

```markdown
## Description
Brief explanation of the change.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Enhancement
- [ ] Documentation
- [ ] Testing

## Test Plan
- [ ] Unit tests added/updated
- [ ] Manual smoke test passed
- [ ] Windows tested (if applicable)

## References
Closes #ISSUE_NUMBER (if applicable)
```

### What Makes a Good PR

- **Small diffs**: prefer many small PRs over one large one
- **Linked to issue**: reference a GitHub issue for context
- **Test plan included**: describe how you tested it
- **Docs updated**: keep README/CHANGELOG/conventions in sync
- **DCO sign-off**: optional but appreciated (see below)

## Developer Certificate of Origin (DCO)

We request (but do not require) commits be signed with:

```bash
git commit -s -m "Your commit message"
```

This adds a `Signed-off-by:` trailer. It indicates you certify that:
- You wrote the code, or
- You have permission to contribute it, and
- The code is not proprietary or under incompatible license

See [developercertificate.org](https://developercertificate.org/) for full text.

## Code Review

When your PR is reviewed:
- Respond to feedback within 48 hours if possible
- Push new commits to address feedback (don't force-push)
- Mark conversations as resolved once addressed
- Tag reviewers again if you've made updates

## Reference Architecture

Before implementing major features, read:
- [docs/architecture.md](docs/architecture.md) — 4 roles, tier system, cascade routing
- [docs/conventions.md](docs/conventions.md) — visual gen, audio, brand, hybrid rules
- [PLAYBOOK.md](PLAYBOOK.md) — 1-page design overview

## Quy tắc vàng (Golden Rules)

From `docs/conventions.md`, the non-negotiable constraints:

1. **Visual gen**: No text→video direct (always text → Flux keyframe → image→video)
2. **Cascade**: Local first, escalate to cloud only when necessary
3. **Reviewer family**: Must differ from executor family (avoid echo chamber)
4. **ComfyUI workflows**: Never commit `.stub` files — must export real JSON
5. **Devlog**: Every meaningful action logged as `kind=source/decision/action/artifact`
6. **Cost gate**: Enforced per video/day/month; cascade fallback on exceed
7. **Privacy**: Never paste client brand assets into free web chat

## Getting Help

- Read `HANDOFF.md` for quick onboarding
- Check `memory/active-context.md` for ongoing work
- Review `docs/decision-log.md` for architectural decisions
- Query devlog: `sqlite3 logs/devlog.sqlite "SELECT ts, kind, content FROM events LIMIT 20"`

## License

Contributions are licensed under MIT (code only). Model weights used retain their original licenses (non-commercial Flux/LTX, etc.— see README.md).
