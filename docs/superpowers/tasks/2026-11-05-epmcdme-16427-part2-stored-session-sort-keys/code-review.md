---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 6f252b4a2840220cd4eaf88e0be33e424dc86a1b
---

# Code review — EPMCDME-16427 part 2 (stored session sort keys)

**Decision: approve** (final round 6, confidence low, at reviewed head `6f252b4a28`). The check round of the SDLC review
approved, and the later review rounds were answered with fixes that a final targeted review approved. Details below; the machine verdicts are `code-review-final.json`
(the final round, `request-changes`, before its fixes) and `code-review-check.json` (the check round, `approve`).

## Rounds

| # | Round | Scope | Verdict | Outcome |
|---|---|---|---|---|
| 1 | SDLC final (`code-review-final.json`) | whole diff at the time, oversized | request-changes, 5 blocking | all 5 fixed |
| 2 | SDLC check (`code-review-check.json`) | the 5 findings | **approve**, medium confidence, 5 of 5 resolved | CR-002 and CR-005 resolved through the approved, documented 825 ms limitation |
| 3 | Standalone, whole branch (after two rebases and the merge of the migration into one revision) | whole diff, partly read (oversized) | request-changes, 3 major | all 3 fixed: savepoint around the sort-key derivation, CLI input validation, `set_rates` wiring test |
| 4 | Second pass, production code (`src/`) | 2,258 lines, fully read | request-changes, 5 | 3 fixed (S cap vs. live set, unstamp when the catalog has no rates, `enable` waits for locked stamps); 2 dismissed: storage guide (changed in this MR, the reviewer saw only `src/`) and the three unbuilt indexes (decision, `scripts/cli_analytics_bench/bench-results.md`) |
| 5 | Second pass, tests (`tests/`) | 3,311 lines, fully read | request-changes, 4 | all 4 fixed: vacuous assertion, shared control row restored, engines closed, held locks rolled back |
| 6 | Targeted, the fixes of rounds 3 to 5 (`55c4ae8739..HEAD`) | 932 lines, fully read | **approve**, 0 findings, low confidence | confidence is low because acceptance criteria outside the fix delta were not graded |

## After the reviewed head

Not covered by a review round: the autoreview finding on retention (stamps of sessions whose source rows retention
deletes are dropped in the transaction of the deletion, with unit and live tests), the removal of the migration's
compatibility statement for earlier forms of the revision (no shared environment applied one), and the wording of the
latency notes in the guide and the benchmark results.

## Found after the fixes

The fix for round 4's S-cap finding narrowed the live set for the cost page too, and the cost page derives the kind
flags of the whole window from it. The live test `test_the_stored_cost_read_equals_todays_statements` caught it; fixed
in `433ca77f9a` (cost page keeps S and the live set over the whole window, the ctx page narrows by the search) with a
unit test, and covered by round 6.

## Checks at the final head

- `make ruff`, the Alembic single-head check, the analytics unit tests (3,754 passed) and the full live suite on
  PostgreSQL 17 and 14 (44 failed, 547 passed each).
- The 44 live failures and the unit failures (106 + 1 error, `main`: 114 + 1) also fail on `main`, in files this
  change does not touch. None fails only on this branch. See `gate-run.json`.

## Known and accepted

- The cost and ctx sorts and the framework filter take seconds at about 1M sessions. There is no agreed latency target
  (825 ms was an assumption); the benchmarks are re-run to show what the work achieves, and the follow-up is a reader
  redesign (`scripts/cli_analytics_bench/bench-results.md`).
- Sessions without a stamp (set S) are computed live; above the cap the request falls back to part 1's statements.
