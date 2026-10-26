# QA Gate Report — epmcdme-15584-sessionend-duration-fallback

**Branch**: EPMCDME-15584_sessionend-duration-fallback
**Runner**: poetry (Makefile targets run via `poetry` directly — `make` not on PATH in this Windows shell)
**Started**: 2026-10-14
**Status**: PASSED

## Gates

| Gate  | Status | Duration | Command | Notes |
|-------|--------|----------|---------|-------|
| lint  | PASS | ~5s | `poetry run ruff format && poetry run ruff check --fix && poetry run ruff check` | All checks passed |
| build | PASS | ~10s | `poetry build` | sdist + wheel built |
| unit (scoped) | PASS | 17.08s | `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/service/analytics/handlers/test_cli_handler.py -q` | 265 passed |
| unit (full suite) | PASS (pre-existing unrelated failures) | 452.76s | `poetry run pytest tests/ -q` | 20404 passed, 199 failed, 204 skipped, 11 errors — all failures confirmed pre-existing on `main` (reproduced `tests/scripts/test_ruff_staged_hook.py` failures on stashed `main` checkout: Windows env has no WSL/bash, so git-hook shell-script tests fail regardless of this change). No failure touches `cli_analytics` code paths. |
| ui    | SKIPPED | — | (n/a) | no UI surface changed — backend-only diff |

## Failure detail

Full-suite failures are concentrated in: `tests/scripts/test_*_hook.py` (require bash/WSL, unavailable on this Windows checkout), `tests/enterprise/mcp_auth/*`, `tests/codemie/migrations/test_credentialtypes_enum_migrations.py` (unicode errors), `tests/codemie/rest_api/router_guards/test_guarded_app_profile.py`, `tests/codemie/service/security/test_logout_token_cleanup_mock_tms.py`. None reference `reader.py`, `cli_analytics_handler.py`, or any file touched by this diff. Verified pre-existing by reproducing `test_ruff_staged_hook.py` failures against `main` (stashed working changes).

## Live verification against real data

Also manually verified via a running backend against real local Postgres data (outside this skill's scope, done ad hoc at user's request): session `5dcc1172-178d-4860-b8da-6de0aa41c04c` — `session_dims.duration_ms = 1220719` vs hook-pair diff `30166`ms. `GET /v1/analytics/cli-analytics/sessions` response for this session returned `duration_ms: 1220719`, confirming the fallback is applied end-to-end.

## Addendum (2026-10-19): MR !4532 review findings (Dmytro_Bondarenko5)

Four threads were opened; three actionable, one a pass-summary with no action item. Checked each
against this task's own prior decisions (`plan.md`, `technical-analysis.md`) before changing
anything, per the receiving-code-review discipline — no prior decision here conflicted with the
reviewer's recommendation.

### note_2466282 (`reader.py:468`) — legitimate, fixed

**Finding:** `session_dims.duration_ms` is read correctly by the reader (this task's own scope), but
the column itself (computed in `rollups.py`, a different file, not touched by this task originally)
was wrong for the exact case this MR exists to fix: a cancelled `SessionEnd`. `agent.session.summary`
is sent on every Stop since codemie-code v0.15.9, each one carrying `is_final`, but `rollups.py` never
read that field — the `sm` join (`_COUNTER_JOINS`) took whichever summary was latest regardless, so a
cancelled `SessionEnd` left `duration_ms` cut at the last Stop, same bug, one layer down. Verified by
reading the live code (`_COUNTER_JOINS`'s `sm` lateral has no `is_final` filter; `_FALLBACK_DUE =
"sm.ts IS NULL AND cur.summary_ts IS NULL"` stops the raw-range fallback forever once any summary, final
or not, is ever stored) — the finding is accurate, not a misreading.

**Fix:** `rollups.py` — `_IS_FINAL_SUMMARY` reads `is_final` off the latest summary's `attrs`
(defaulting to `true` when absent, so an older client's summary keeps today's exact behavior: only an
explicit `is_final: false` is new). `fb` (the raw-range lateral) now also computes when the latest
summary is provisional, not only when none was ever stored (`_PROVISIONAL_SUMMARY`), so `fb.last_ts` is
available in that case. `ended_at`/`duration_ms` (`_ENDED_AT`/`_DURATION_MS`) take the summary's own
`ended_at` outright when final, and the *later* of the summary's `ended_at` and `fb.last_ts` when
provisional. Every other counter is unaffected: `_summary_or` still takes the summary value whenever
`sm.ts IS NOT NULL`, so the extra `fb.*` columns computed for a provisional summary are simply unused
elsewhere.

**History respected, not repeated:** this is additive to this task's own fallback mechanism (a column
this task reads, not one it invented) — there was no prior "is_final can be ignored" decision on
record to push back on or accidentally re-break.

**Tests:** new live-Postgres file
`tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py` — three
tests: (1) a non-final Stop summary followed by later raw activity takes the later timestamp (RED
confirmed against pre-fix `rollups.py`: `ended_at` stuck at the Stop, reproducing the reviewer's exact
shape; GREEN after); (2) a final summary stays authoritative even with an unrelated, later raw record
(regression guard for the case review already accepted); (3) repeated refreshes of a final-summary
session give the same, stable duration (AC5's "repeated normal exits").

**AC3 tolerance documented:** `.ai-run/guides/integration/cli-analytics-storage.md`, the
`ended_at`/`duration_ms` row, gained a paragraph stating the tolerance in concrete terms (exact once a
final summary or a real `SessionEnd` lands; otherwise bounded by the gap to the latest raw record,
closed by the next event).

### note_2466285 (`test_reader.py:292`) — legitimate, fixed by the same work

**Finding:** the existing tests assert SQL-substring shape only, not the cancelled-SessionEnd
behaviour. Correct — `test_session_durations_prefer_duration_ms_fallback` and siblings only check that
the reader's SQL *mentions* `session_duration_ms`/`coalesce`; none of them exercise the real rollup.
The three live tests added for note_2466282 are exactly the missing behavioral coverage this thread
asked for (ingest-shaped rows, a real `RollupRefresher.refresh()`, asserted against the real
`session_dims` row and `get_session_durations`), so no separate work was needed beyond what note_2466282
already required.

### note_2466284 — scope/process finding, not a code bug; reassessed, not fixed here

**Finding:** AC1 ("a normal `/exit` reliably records session completion") has no codemie-code change,
and the MR marks it out of scope. The reviewer's concrete worry was that, combined with note_2466282's
gap, *neither* of AC1's two accepted alternatives (a reliable `SessionEnd`, or a reliable server-side
fallback) held for the current CLI.

**Reassessment after the note_2466282 fix:** the second alternative — "a reliable final-timestamp
alternative" — now holds much more fully than before. Previously a cancelled `SessionEnd` with only a
non-final Stop summary left the duration permanently wrong (truncated at the last Stop, no path to
correct it short of a real `SessionEnd` arriving later). Now it is bounded by the gap to the session's
*latest* raw record and keeps closing itself on every refresh as more raw activity (or, eventually, the
real `SessionEnd`) arrives — the server-side alternative AC1 allows for. This is still not identical to
a reliably recorded `SessionEnd` (an idle session that sends no further raw activity after its last
Stop still shows the Stop's time, not the true, later exit instant, until something else arrives), so
it does not fully discharge AC1 on its own.

**This task does not implement the codemie-code side** (a separate repository, out of reach from this
checkout) and does not touch AC1's wording — that is the user's/PO's call, not a code change. Flagging
for the user: either (a) reply to the reviewer that the server-side alternative is now materially
better (point to this fix) and ask whether that satisfies AC1 as currently worded, or (b) ask the PO to
amend AC1 explicitly to the server-side alternative, per the reviewer's own suggested wording. No
action taken on this thread beyond this reassessment.

**Verification (2026-10-19):**

```
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py -v
# 3 passed (against postgresql://postgres:password@localhost:5433/postgres, a disposable Postgres 17
# instance with this branch's own schema migrated onto it — the shared dev analytics database's
# container had been recreated empty since this task's original 2026-10-14 run, so schema + partitions
# were re-created via run_migrations()/PartitionMaintainer before seeding)

poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_rollups.py \
  tests/codemie/repository/cli_analytics/postgres/test_reader.py -q
# 244 passed

TEST_LOCAL_PG_URL=postgresql://postgres:password@localhost:5433/postgres poetry run pytest \
  tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py \
  tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -q
# 50 passed (no regression in the sibling MR's own live dispatch suite or the handler tests)

poetry run ruff format src/codemie/repository/cli_analytics/postgres/rollups.py \
  tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py
poetry run ruff check src/codemie/repository/cli_analytics/postgres/rollups.py \
  tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py --fix
# All checks passed
```

## Drift signal

no (fixes an open reviewer-found gap in this task's own duration fallback; no scope change)

## Addendum (2026-10-19, second round): `/sdlc-factory:code-review` findings CR-001–CR-004

A full 3-lens code review (blind, edge-case, acceptance-against-the-5-ACs-plus-note_2466282) was run
against the branch after the first-round fix above landed. Verdict: `request-changes`, 4 major
findings, persisted (frozen, not to be edited) at
`docs/superpowers/reviews/2026-10-19-epmcdme-15584-mr4532/code-review-final.json`. All four fixed:

**CR-001 (duration_ms has no ended_at>=started_at guard).** `rollups.py`'s new `_DURATION_MS` is now
wrapped in the same `CASE WHEN ended >= started THEN ... END` guard this file already uses for
`_SUBAGENT_VALUES`'s own `duration_ms` — NULL, not a negative number, on clock skew or malformed
client attrs. Pinned by a new SQL-shape test (`test_a_computed_session_duration_is_never_negative`)
and a new live test (`test_live_an_ended_at_before_the_started_at_gives_a_null_duration_not_a_negative_one`,
RED-confirmed against the pre-fix code: `-30000` instead of `None`). Defense in depth added at all
three `reader.py` read sites (`get_session_durations`, `get_session_cost_facts`, `get_session_detail_meta`)
and `cli_analytics_handler.py`'s `get_sessions` — each clamps a negative value to 0 via `CASE`, not
`greatest(0, ...)`, specifically so a genuine NULL ("duration unknown") is not coerced into a false 0
("a real, zero-length session") the way `greatest()` would.

**CR-002 (is_final cast can throw on malformed attrs).** `_IS_FINAL_SUMMARY`'s bare `::boolean` cast is
now guarded: `CASE WHEN lower(text) IN ('true','false') THEN text::boolean ELSE true END` (`_IS_FINAL_TEXT`/
`_IS_FINAL_SUMMARY`). Pinned by `test_a_malformed_is_final_value_defaults_to_final_instead_of_raising`
and a live test (`test_live_a_malformed_is_final_value_does_not_fail_the_refresh`). Note: the first
attempt at a RED reproduction used `is_final: "1"`, which turned out to be a *valid* Postgres
`::boolean` literal (Postgres also accepts `1`/`0`, `yes`/`no`, `t`/`f`, `y`/`n`, `on`/`off`, not just
`true`/`false`) and passed even on the pre-fix code — the live test was corrected to use `"unknown"`,
genuinely outside that whole accepted set, and RED-confirmed (refresh raised) before the fix, GREEN after.

**CR-003 (documented tolerance overstates its own guarantee).** `.ai-run/guides/integration/cli-analytics-storage.md`'s
tolerance paragraph now explicitly discloses the idle-tail case: when a session sends no further raw
telemetry or summary after its last Stop (the ticket's own primary reported shape — a normal `/exit`
with a cancelled `SessionEnd` and nothing after), the undercount is **not** bounded by this mechanism
and persists indefinitely, same as before this fix. Docs-only change, no test.

**CR-004 (fb LATERAL now runs on every provisional summary).** Benchmarked honestly per the fix
directive's own preference (benchmark first; restructure only if clean and low-risk — judged the
restructuring too invasive given the complex multi-way `_COUNTER_JOINS` LATERAL structure). A
standalone, non-committed script seeded one session with 100,000 `log_events` rows (5x the scale the
sibling EPMCDME-15582 review used) and timed `RollupRefresher.refresh()` once per summary shape,
3 repetitions each, against the disposable test Postgres:

```
FINAL summary runs:       ['0.906', '0.989', '1.448'] avg=1.114s
PROVISIONAL summary runs: ['0.911', '1.061', '1.280'] avg=1.084s
Delta: -0.030s (-2.7%, i.e. no measurable regression -- within run-to-run noise)
```

Conclusion: the theoretical concern does not manifest as a measurable regression even at 5x stress
volume for a single session. No restructuring was applied — doing so now would add complexity for no
measured benefit. If a real regression ever surfaces at production scale (many concurrently-dirty
sessions, not one), the fix directive's own suggested restructuring (compute only `fb.last_ts` on the
provisional-only path) remains available and is now informed by this honest baseline.

**Verification (2026-10-19, second round):**

```
poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_rollups.py \
  tests/codemie/repository/cli_analytics/postgres/test_reader.py -q
# 243 passed

TEST_LOCAL_PG_URL=postgresql://postgres:password@localhost:5433/postgres poetry run pytest \
  tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py \
  tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py -q
# 12 passed (5 in test_live_session_duration_fallback.py, including the 2 new CR-001/CR-002 live tests)

poetry run pytest tests/codemie/service/analytics/ -q -k session
# 29 passed

poetry run ruff format src/codemie/repository/cli_analytics/postgres/reader.py \
  src/codemie/service/analytics/handlers/cli_analytics_handler.py
poetry run ruff check src/codemie/repository/cli_analytics/postgres/reader.py \
  src/codemie/service/analytics/handlers/cli_analytics_handler.py --fix
# All checks passed
```

`code-review-final.json` was left unedited (frozen round-1 verdict snapshot, per convention); a
future re-check against these fixes would produce a separate `code-review-check.json`, not edit this file.

## Drift signal (second round)

no (fixes 4 findings from a code review of this same task's own first-round fix; no scope change)
