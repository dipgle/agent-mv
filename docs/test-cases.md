# Test Cases

Human-readable mirror of the `test_cases` table in `logs/devlog.sqlite`.
The sqlite table is the source of truth; this file is for review and onboarding.

Conventions:
- ID format: `TC-001`, `TC-002`, … zero-padded, monotonically increasing.
- Every TC is linked to exactly one UC (`use_case_id`).
- Status reflects the latest `test_runs` row: `pending | pass | fail | skipped`.
- Execution history lives in the `test_runs` table — query sqlite for trends.

---

## UC-000 — Template

### TC-000 — Template (delete when first real TC is added)

- **Use case:** UC-000
- **Status:** pending
- **Steps:**
  1. step
  2. step
- **Expected:** what success looks like
- **Last run:** —
- **Last result:** —
- **Notes:**
