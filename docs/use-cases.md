# Use Cases

Human-readable mirror of the `use_cases` table in `logs/devlog.sqlite`.
The sqlite table is the source of truth; this file is for review and onboarding.

Conventions:
- ID format: `UC-001`, `UC-002`, … zero-padded, monotonically increasing.
- Status: `draft` → `active` → `done` (or `deprecated`).
- Every UC has ≥ 1 test case in [test-cases.md](./test-cases.md).

---

## UC-000 — Template (delete when first real UC is added)

- **Actor:** end user
- **Status:** draft
- **Preconditions:** what must be true before this flow starts
- **Main flow:**
  1. step
  2. step
  3. step
- **Alt flow / errors:**
  - condition → outcome
- **Test cases:** TC-000
