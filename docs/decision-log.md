# Decision Log — init-project

## 2026-06-04 (later) — HARD RULE #7 added: CHOOSE DIAGRAM TYPE BY CONTENT

**Decision:** Add "CHOOSE DIAGRAM TYPE BY CONTENT" as HARD RULE #7 to
`CLAUDE.md`, positioned after rule 6 (REVIEW VISUAL ARTIFACTS). Synced
to `codetrail/template/CLAUDE.md`.

**Trigger:** Six-variant audit of cross-project inbox flow content
(same source, different diagram types + tools). Findings:

| Variant | Score | Type | Tool |
|---|---|---|---|
| V5 mermaid sequence diagram | 8/10 | sequence | mermaid |
| V6 prose + ASCII | 7/10 | prose | none |
| V1 minimal 4-box | 6/10 | container | d2 |
| V2 container hierarchy | 5/10 | container | d2 |
| V3 dark + codetrail tokens | 4/10 | container | d2 |
| V4 sketch mode | 4/10 | container | d2 |

**Lesson:** TYPE choice dominates TOOL choice. The inbox flow is a
temporal protocol; sequence diagram (V5) matched its shape and won by
3 points over the best container attempt. Container variants (V1-V4)
all lost regardless of palette, tool, or polish — they forced topology
onto temporal content.

**Two sharper rules fell out of the audit:**
1. PROSE FIRST: V6 with ASCII + 5-sentence lifecycle scored 7/10 ahead
   of three polished d2 attempts. If content fits <5 sentences without
   loss, prose wins.
2. ONE ACCENT, ENFORCED: V3 picked up purple for "writes own" edges
   alongside mint for "send_message". Two colors → eye pattern-matches
   colors instead of reading the message → instant 2-3 point drop.

**Type-by-content table** (the body of the new rule): sequence for
protocol/lifecycle, ER for entity/relationship, container for
module/dependency, state for FSM, flowchart for linear pipeline ≤7,
tree for hierarchy, PROSE for plain structural facts.

**Acceptance:**
- [x] Rule in `init-project/CLAUDE.md` after rule 6
- [x] Synced to `codetrail/template/CLAUDE.md`
- [x] Decision-log entry (this one) + devlog `kind=decision`
- [x] Smoke-check: fresh `np /tmp/smoke-rule7` scaffold inherits rule 7

**Related:**
- HARD RULE #6 (REVIEW VISUAL ARTIFACTS) — companion rule for
  post-draw audit; #7 is pre-draw type selection
- 6-variant rendered artifacts retained at `/tmp/diagram-demo/`
  (v1-v5 PNGs + v6 prose) as evidence



## 2026-06-04 — HARD RULE #6 added to CLAUDE.md template

**Decision:** Add "REVIEW VISUAL ARTIFACTS BEFORE REPORTING DONE" as HARD
RULE #6 to `CLAUDE.md`, positioned after rule 5 (IMPLEMENT TO GREEN).
Synced to `~/Documents/projects/AI/codetrail/template/CLAUDE.md` so OSS
users get it on fresh scaffold via `np` / `adopt`.

**Trigger:** REQ-INIT-001 cross-session request, opened by tfl5 session
2026-06-04, surfaced in `TODO.md`. Source incident: 3 broken SVG iterations
of `tfl5/docs/architecture.svg` (v1 "vỡ lung tung", v2 rainbow palette,
v3 section-overlap caught only after user demanded crop-by-crop inspection).

**Why a rule and not just discipline:** the same class of defect (text
overflow, CSS cascade silently swallowing colors, connector lines cutting
through unrelated boxes, container bounds not expanding to nested
`<g transform>`) recurred across sessions despite ad-hoc feedback. Hà's
auto-memory entry `feedback_review_visual_artifacts.md` covers it globally
for the current user; landing in `CLAUDE.md` hardens it into the project
template so it survives memory pruning and propagates to every scaffolded
project automatically.

**Workflow mandated by the rule:** render → strip padding → split into
4-8 logical sections via Python + Pillow → read each crop → audit against
source → document defects → re-render only changed area → verify → THEN
report done.

**Alternatives considered:**
- Discipline-only (keep as ad-hoc feedback): rejected because the rule
  has already failed under discipline alone three times today
- PostToolUse hook printing reminder: deferred (optional follow-up noted
  in TODO.md) — v1 is the rule itself; add a hook only if the rule still
  gets skipped after landing

**Acceptance:**
- [x] Rule lands in `init-project/CLAUDE.md` (L126–166)
- [x] Rule synced to `codetrail/template/CLAUDE.md` for OSS propagation
- [x] CLAUDE.md still within meta-rule budget (rules excluded; non-rules
      section unchanged at ~58 lines)
- [x] This decision-log entry mirrors the policy change
- [x] `kind=decision` logged to devlog
- [x] Smoke-check: scaffold a fresh `np` project at `/tmp/smoke-rule6`,
      confirmed CLAUDE.md contains rule 6 (1 occurrence as expected)

**Related:**
- [REQ-INIT-001 in TODO.md](../TODO.md)
- [REQ-INIT-002 — diagram redesign comms with tfl5](../TODO.md) (separate,
  still in-flight, MSG-002 posted to comms thread)
- User memory: `feedback_review_visual_artifacts.md`
- Sister artifact: `~/Documents/projects/AI/_comms/arch-redesign-thread.md`
