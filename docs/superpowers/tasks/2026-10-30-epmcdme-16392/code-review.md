---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 4e6d3ed8d6fba55a3fff899ff85b02c6cab8b714
---

# Code review - 2026-10-30-epmcdme-16392

**Decision:** approve (check round, confidence high)

**Reviewed head:** `4e6d3ed` · **Rounds:** final → check

Rebased onto `main` `780a7626837275f3c838de297f96897fae5335ef` on 2026-11-03. The reviewed source maps to `2f8afd53c`; range-diff confirms unchanged task patches apart from retaining main's `_BLANKS` import. Targeted post-rebase checks: 248 passed. This publication-path change does not change the reviewed code or verdict.

**Closed:** 1 fixed · 0 moot · 0 decided

Reviewer remediation (2026-11-03): inline request pairing and the window/matched/usage CTEs now live in `inline_skill_attribution.py`. The reader's generated dispatch SQL and bound parameters are unchanged byte-for-byte (SQL SHA-256 `4a94422a1f3fbda77dd315b50a8cefdc4ab13009beedf2a9d09920c43ad706b0`). Rebased onto main `b658e6f31`; all 12 existing patches remain equivalent. Reader/session-derived unit tests: 249 passed; changed-file Ruff check and format passed. This is a behavior-preserving extraction, not a new factory review round.

CR-001 resolved: DISTINCT in dispatch_fork (reader.py:1037) collapses rows identical in ts/skill_name/prompt_id/agent_id/fork_ts to one dispatch, so inline_window yields one window and one timeline row. It changes no other row: fork start rows are gated by NOT EXISTS on agent_id, Skill-call and session-total paths do not read dispatch_fork duplicates, and distinct dispatches differ in at least one key column. Live evidence was taken from the dispatch context, not rerun.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1 | pass | inline_usage supplies duration, cost, four token kinds and per-model values for fork-less dispatches |
| AC2 | pass | Reuses _candidates/_one_to_one/_resolved tiers 1-3 over the whole session |
| AC3 | partial (final round) | Bounded by prompt_id and next same-skill start; duplicate identical dispatch rows would double count |
| AC4 | pass | Fork branch unchanged; fork rows still use au values |
| AC5 | pass | Empty prompt_id excluded from inline_window; missing usage gives coalesce 0 |
| AC6 | pass | Pairing computed once in inline_attributed; inline_matched materialized |
| live DB tests | pending-stage-7 | SQL behavior proven only by running the live dispatch gate tests |

Standards: commit-format na · code-quality na · security na
