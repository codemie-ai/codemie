---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: a1b9a148bc56333122bdbdf085889d9a1b56b220
---

# Code review — 2026-10-30-epmcdme-16435-summary-skill-names

**Decision:** approve (check round, confidence final medium · check high)  
**Reviewed head:** `a1b9a14` · **Rounds:** final → check

**Closed:** 4 fixed · 0 moot · 0 decided

All four prior blocking findings (CR-001..CR-004) are resolved on current source: guide A5 note present, pg_framework_rows now skipped only when evidence is cached, and the added tests target the A5 query/parse/build and person-view call sites. Tests were read, not executed (Stage 7 owns execution); key_for has no dedicated test.

## Open — 0

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| R1 unconditional name union, no summary CASE, NULL when empty | pass | CLASSIFIER_NAMES_SQL rewritten; one constant feeds classifier and reader |
| R2 person view: Postgres names merged with ES keys, ES fallback | pass | _with_postgres_skill_names with try/except fallback |
| R2 session id check recorded before wiring | pass | r2-verification.md present; content not audited |
| R3 docs: classifier-input sentence updated to unconditional union | pass | delivery_framework row rewritten |
| R3 docs: short A5 source-rule note | fail (final round) | guide has no A5 / get_framework_sessions note |
| R4 rewrite exact-SQL assertions | pass | assertions replaced with union fragments |
| R4 live test summary skills={} with STLC commands and malformed commands | pass | test_live_summary_skill_names.py |
| R4 live test kind-2 and session_skills union | pass | test_summary_session_unions_dispatch_and_session_skills |
| R4 unit tests for person-view path | partial (final round) | no test of _person_live, _person_session_extras or sessions_extra_body |
| R4 tests run from worktree src | pending-stage-7 | execution evidence only |
| R5 reader method + port, window [start,end) | partial (final round) | 5-tuple with raw repo_remote, normalized in service layer; deliberate deviation |
| R5 user mapping and repository match | pass | worker._merge_postgres_framework |
| R5 query in API process before dispatch | partial (final round) | skipped when inputs are cached but evidence is not |
| R5 merge rule min(sessions, max(es, pg)) | pass | merge_framework |
| R5 ES fallback never fails job | pass | fallbacks tested |
| R5 INPUTS_VERSION bumped | pass | 3 -> 4, hash test |
| R5 verification recorded before wiring | pass | r5-verification.md present; content not audited |
| R5 tests: sessions_body, parse_sessions, _build_sess/key_for | partial (final round) | sessions_body, parse_sessions, pipeline tests absent |
| Non-goals respected | pass | no migration, denylist, CLI change |
| 10. PG STLC with no ES skills makes A5 framework > 0 | pass | except cached-inputs path |
| 15. Unit tests for sessions_body, parse_sessions, pipeline merge, reader, live | partial (final round) | sessions_body and parse_sessions tests absent |
| 17. make ruff and touched tests pass from worktree | pending-stage-7 | owned by Stage 7 |

Standards: commit-format pass · code-quality pass · security pass
