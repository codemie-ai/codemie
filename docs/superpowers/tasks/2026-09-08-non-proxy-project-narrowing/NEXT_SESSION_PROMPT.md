# Next Session — Prompt Template

## Copy-Paste Prompt for Next Session

```
I'm continuing work on EPMCDME-14455 (refuse disallowed models).

**Current state:**
- Branch: EPMCDME-14455-Refuse-disallowed-models-instead-of-silently-substituting
- Task directory: docs/superpowers/tasks/2026-09-08-non-proxy-project-narrowing/
- Stage: 5 Complete (Code Review) — Ready for Stage 6 (Validation)
- Status: All code review findings resolved ✅; all 40 tests passing

**What I want to do:**
Proceed to Stage 6 validation (qa-gates), then Stage 7 (handoff).

**Prior artifacts available:**
- HANDOFF.md (overview of completed work)
- FIXUP_COMPLETE.md (detailed fix summary)
- code-review-check.json (check-round verdict: approved)
- plan.md (9-task decomposition)
- technical-analysis.md (codebase findings)

Please use sdlc-light skill to advance to Stage 6.
```

## What This Accomplishes

1. Provides all context needed to pick up work
2. References the handoff document and current stage
3. Asks for explicit next-stage progression via sdlc-light
4. Avoids re-running code review or re-implementing fixes

## Expected Flow

1. Claude reads HANDOFF.md to orient
2. Claude invokes sdlc-light skill with `mode: "sync"` (skip research; go to Stage 6)
3. Feature verification runs (if UI-touched) or is skipped
4. Stage 7: Actual complexity assessment
5. Ready for merge

---

**Estimated time for next session:** 15-20 minutes (validation + handoff)
