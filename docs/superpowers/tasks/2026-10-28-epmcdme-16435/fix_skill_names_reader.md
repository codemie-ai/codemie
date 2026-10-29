# Fix: list/details delivery-framework reader misses hook-only skills

MR !4664 review thread (unresolved): `get_skill_names_by_session` reads only `session_skills`
(populated solely from OTel `skill_activated`). A session known only through hooks / dispatch /
summary gets an empty skill list, so `classify_delivery_framework([])` returns "Pure chat" and the
STLC Factory rule never applies in the list and details views.

Decision (owner-approved): **Option A** — make the reader use the same skill-name source as
`session_derived._READ`; keep the row shape so callers and the `framework` filter stay unchanged.

## Facts (from recon)

- Reader: `src/codemie/repository/cli_analytics/postgres/reader.py:604-615`
  (`get_skill_names_by_session`, returns `session_id, skill_names`; port at
  `src/codemie/repository/cli_analytics/ports.py:177`). Sessions with no rows are absent today.
- Callers (do not change): `src/codemie/service/analytics/handlers/cli_analytics_handler.py`
  lines ~698/737 (list) and ~797/894-895 (details).
- Source to align with: `src/codemie/repository/cli_analytics/postgres/session_derived.py:31-48`
  (`_READ`, `_SKILLS_OBJECT`): summary present (`sd.summary_ts IS NOT NULL`) → keys of `sd.skills`
  only; otherwise `UNION` of `session_skills.skill_name` and keys of `sd.skills`; empty names
  excluded; `COLLATE "C"` ordering.
- Existing SQL-shape test to mirror: `tests/codemie/repository/cli_analytics/postgres/test_session_derived.py:144-160`.
- Handler tests use `AsyncMock` repos (`tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`,
  `make_detail_handler` ~1040-1052); no test asserts a classification today.
- Classifier: `src/codemie/service/analytics/delivery_framework.py`; STLC rule tests in
  `tests/codemie/service/analytics/test_delivery_framework.py:61-86`.

## Do

1. Put the skill-names SQL in ONE place and use it from both `session_derived._READ` and
   `reader.get_skill_names_by_session`, so the two cannot drift. Prefer a module-level constant /
   small helper in `session_derived.py` (or a neutral shared module if that avoids a circular
   import). `_READ` behaviour must not change (its existing test must still pass unchanged).
2. Rewrite `get_skill_names_by_session` to read from `session_dims` with that fragment. Keep:
   empty input → `[]` with no query; ids de-duplicated via `dict.fromkeys`; result columns
   `session_id`, `skill_names` (array ordered `COLLATE "C"`); the same parameter style and
   scoping/guards the neighbouring reader methods use. Update its docstring (no longer only
   "activated skills"). A session with no skills may be absent from the result or return an empty
   array — callers already use `.get(sid, [])` / `or []`, keep that working.
3. Tests (follow the repo's testing guide `.ai-run/guides/testing/testing-patterns.md`):
   - Reader: SQL-shape assertion that the query reads `session_dims`, covers both branches
     (summary → `skills` keys only; no summary → `session_skills` UNION `skills` keys) and is
     identical in source to what `_READ` uses.
   - List and details handler tests: a hook-only session (repo mock returns `stlc-*` skill names
     with no OTel rows) is classified `STLC Factory` (use the label the config/classifier test
     uses), and a session with no skills stays "Pure chat". Use the real classifier with the
     customer-config rule, or the same fixture approach `test_delivery_framework.py` uses.
4. Scope: touch only the reader, the shared fragment, `session_derived.py` if needed, and the
   tests. No config, migration, dependency, or handler changes. English only. No AI trailers.

## Gates

- `make ruff` (or the exact command in `.ai-run/guides/quality-gates.md`) clean.
- Run only the narrow relevant tests: the reader, `test_session_derived.py`, the cli_analytics
  handler tests, `test_delivery_framework.py`, `test_rollups.py`.
- No git mutations (architect commits).

## Report

Write `CHECKPOINT` to `docs/superpowers/tasks/2026-10-28-epmcdme-16435/fix_skill_names_reader.CHECKPOINT.md`:
files changed (file:line), what was shared, tests added, exact commands run with totals, anything
not done or uncertain.
