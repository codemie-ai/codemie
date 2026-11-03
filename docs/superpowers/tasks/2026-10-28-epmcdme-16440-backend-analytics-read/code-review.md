---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 16b739be6cf15bcc844cc967f6e6954ef1339e27
---

# Code review — 2026-10-28-epmcdme-16440-backend-analytics-read

**Decision:** approve (check round, confidence final low · check medium)  
**Reviewed head:** `16b739b` · **Rounds:** final → check

**Closed:** 4 fixed · 0 moot · 0 decided

All four prior blocking findings resolved on source: CR-001 shared sql_norm/_py_norm with identical trim sets (octal \013 for VT) used in Personal detection, owner match and vocabulary; CR-002 per-aggregate class lists derived from each aggregate's own contributing sessions; CR-003 confirmed false on the real User with production role logic untouched; CR-004 live list-mode delivery_framework test added and the dispatch-gate helper only drops class lists. Residual: Python vs Postgres lower() on non-ASCII is uncovered; openapi baseline not verified by the real snapshot test; live-PG results are Stage 7 evidence.

## Status addendum (2026-10-28; the generated text above is kept as written)

- **Final reviewed head:** `16b739be6cf15bcc844cc967f6e6954ef1339e27` (tree `2be2015e508e21c8c532d9dbb76f91ca0354def1`). Code evidence is bound to it; later commits (`4c5c33395`, planning artifacts) are artifacts-only. Review history, kept separate: automated full review at `e915e4034` (request-changes, CR-001..CR-004, confidence low, the 5363-line diff was not read in full); one fix-up, committed as `16b739be6` (CR-003 dismissed on source: `user.py:124`); automated check approve (confidence medium); the user's independent contract review of `19293cd37..16b739be6` found no remaining blocking code finding. Historic findings stay in `code-review-final.local.json`; the AC1 row below was re-evaluated at the final head.
- **QA is CONDITIONAL, not a clean full-suite approval** (`gate-run.json`, head `16b739be6`). Clean: ruff, license, build, gitleaks, 15 changed unit files (1096 passed, 14 skipped), live owner-scope (17 passed on the committed SHA), 4 dispatch cases. Accepted baseline failures with exact-baseline evidence: 17 unit (15 `test_dirty.py` calendar edge, 2 `test_settings.py`) and 1 live (`last_calendar_day`, `OSError` Errno 22), mapped to the first child's baseline logs.
- **Limits:** the OpenAPI snapshot cannot be verified byte for byte locally. The package `codemie_enterprise` is absent, so 24 LLM Proxy operations cannot be generated. The available operations and components match the live output; the 24 retain their parent contents. The QA run used an environment wrapper and a local QA-only msgraph shim (a `sitecustomize.py` that fakes the `msgraph.generated.users` subtree with `MagicMock` modules so `main.py` can import; it touches no route or schema under test and is not part of the application or its tests; the file was removed from the delivery branch and a copy is kept outside the repository). It was needed because the shared venv lacks `msgraph.generated.users`. The router-guard results quoted here were produced WITH this shim, not without it. WinError 10106 is a process-launch environment issue (reproduced on the clean parent and on `381dc7fdd`).
- **Still required before any MR:** the full canonical OpenAPI snapshot test with `codemie_enterprise` (CI or Linux), Sonar and the pre-push hook, and the normal integration checks. Not run here: the whole repository suite, PG14, the rest of the live directory. Python `lower()` against Postgres `lower()` on non-ASCII letters is not covered.
- **Combined integration with EPMCDME-16441 (!4652, head `e53fde4e`) is pending and not claimed:** only SQL-text seam evidence exists; `detail_gate` must be carried by the `_token_rows` detail caller at merge; the shared HMAC/log wiring from the first child is pending too.
- Not MR-ready and not merge-ready.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| AC1 Fields: scalar subscription_type/project_origin on rows and detail; sorted distinct lists on every aggregate model | pass | re-evaluated at the committed fix-up 16b739be6: the four /efficiency models now carry class lists built from each aggregate's own contributing sessions (CR-002 resolved). The first round's 'partial' (no lists on /efficiency) is historical and stays in code-review-final.local.json |
| AC2 Filters on all selection-backed routes; unknown/none match NULL/empty | pass |  |
| AC3 Legacy rows read as unknown/none, stay listed | pass |  |
| AC4 Owner self-view on /sessions and detail only | pass | see CR-003 for project-admin list behavior |
| AC4a personal_only selector only on /sessions | pass |  |
| AC5 Non-owner empty list, 404 on detail | pass |  |
| AC6 Super-admin and auditor never see another user's Personal | pass |  |
| AC7 Email handling case/whitespace-insensitive; NULL never matches | pass |  |
| AC8 Shared spend excludes Personal | pass | see CR-001 for padded-value edge |
| AC9 Cost-less and subagent rows; SQL-text tests for !4652 shapes | pass | combined landing with !4652 not verified, by design |
| AC10 Detail and skills truth table; absent scope denies | pass |  |
| AC11 Unchanged behaviors (binding-only unlisted, M not in lists) | pending-stage-7 | execution evidence |
| AC12 Rollout documentation updated | pass |  |
| AC13 openapi baseline regenerated; tests added | pending-stage-7 | live-PG and suite results are Stage 7 evidence |

Standards: commit-format pass · code-quality pass · security pass
