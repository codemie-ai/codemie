# EPMCDME-16528 Backend: Readable Session Title Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve a readable `prompt` in CLI Analytics Session List/Detail: pasted-content tags stripped, explicit `--name` (`session_title`) taking precedence.

**Architecture:** Strip tags in the `session_dims.first_prompt` rollup SQL (source of truth) and again in a pure Python helper at read time (covers stored rows). Add `session_dims.session_title` (latest-wins, bounded) fed through the existing `_COMMON_TEXT` latest-value mechanism; the handler picks `session_title` else cleaned first prompt.

**Tech Stack:** Python, FastAPI, asyncpg/Postgres, alembic, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-29-epmcdme-16528-backend-pasted-content-first-prompt/spec.md` (technical analysis in the same dir). Paths below are relative to the repo root `/Users/vponkratina/work/ai/codemie`.

Commit per task using the repository's existing convention (`EPMCDME-16528: Description`). No push or PR steps.

## Global Constraints

- Tag regex (ARE): `</?pasted_content(\s[^>]*)?(>|$)`, flag `g`; remove tag text only, keep enclosed text byte-for-byte, then trim `" \t\r\n"` both ends (default `btrim` trims spaces only). Python uses `\Z`, not `$`.
- A cut inside the tag name is not recovered; `<pasted_contentx ...>` is left alone.
- `session_title` cut to 200 chars after trim, empty -> NULL. `prompt_original` is never read, served, searched or persisted.
- No REST model/OpenAPI change, no data-repair UPDATE, no backfill change, no change to `_IS_AUTO_PROMPT` or to `prompt_body` at ingest.
- New files carry the Apache license header (copy from a neighbour); comments describe what the code is, with no ticket/history references.
- Live-PG tests: always run with `TEST_LOCAL_PG_URL=postgresql://postgres:password@localhost:55432/postgres` (container `pg-16528`), `-n 0`. Never port 5432 (the live-test default when unset). Lint with `make ruff`.

## Review Focus

- A paste block that cleans to empty as the first prompt: next prompt wins; the stored value is never replaced by `''` (T2).
- Sentinel wrapped in a paste block (`<pasted_content id="a">\n/clear\n</pasted_content id="a">`) is skipped (T2).
- Whitespace-only latest `session_title`: stored name kept (T4).
- Named session: search by name word AND by real first-prompt word AND repository/branch; `worst_session_prompt` never the name (T5).
- Stored row that still holds markup (pre-fix) is served clean; `first_prompt` cleaning to `''` serves `''`, not an error (T5).

## Negative-constraint pass

Honoured by: no block removal / no sentinel entry for the tag (T2 strips tags only); `prompt_original` untouched (no task reads it; T4 ingests nothing new); no REST change (T5 keeps `prompt: str`, extra search value held in a local dict); `session_dims.title` unused (T4 uses new column); no UPDATE/backfill (T3 schema-only); `_IS_AUTO_PROMPT` untouched (T2 asserts it); no multi-version PG infra (T2 parity test runs on the one DB).

---

### Task 1: Pure strip helper and shared pattern

**Files:**
- Modify: `src/codemie/repository/cli_analytics/vocabulary.py` (after the sentinel block, ~line 147)
- Test: `tests/codemie/repository/cli_analytics/test_vocabulary.py`

**Interfaces:**
- Produces: `PASTED_CONTENT_TAG_SQL: str` (the ARE pattern above) and `strip_pasted_content(text: str) -> str` (same semantics incl. trim).

- [ ] **Step 1: Write failing tests** in `test_vocabulary.py`: parametrize over the 9 corpus cases of `/tmp/repro-16528/regex-corpus.sql` (copy the strings into the test as `PARITY_CORPUS: list[str]`, for reuse by T2) with expected outputs: whole paste -> `Step 1\nStep 2`; typed+paste -> `Perform the test\n\nA`; two blocks -> `one and two`; unterminated -> `unterminated text with no close`; orphan close -> `text`; `<pasted_content id="trunc` -> `''`; `<pasted_content` -> `''`; plain -> unchanged; `<pasted_contentx id="q">not ours</pasted_contentx>` -> unchanged. Add: inner newlines preserved byte-for-byte; a trailing `"\n"` after a tag does not make `\Z` differ from SQL `$` (`'<pasted_content id="a\n'` stays tag-free); `''` -> `''`.
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/repository/cli_analytics/test_vocabulary.py -n 0 -q`; expect ImportError/FAIL.
- [ ] **Step 3:** Implement both names in `vocabulary.py`; compile a Python pattern `</?pasted_content(?:\s[^>]*)?(?:>|\Z)` once, `strip_pasted_content` = `.sub('', text).strip(" \t\r\n")`.
- [ ] **Step 4:** Rerun; expect PASS.

Test-first: yes — `test_strip_pasted_content_*` fails with ImportError until the helper exists.

### Task 2: Clean `first_prompt` in the rollup SQL

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/rollups.py:176-212` (`_PROMPT_OK`, `_earliest`, `_earliest_merged`) and the upsert at `:1937`
- Test: `tests/codemie/repository/cli_analytics/postgres/test_rollups.py`; create `tests/codemie/repository/cli_analytics/postgres/test_live_first_prompt.py`

**Interfaces:**
- Consumes: `PASTED_CONTENT_TAG_SQL` (T1).
- Produces: `_clean_prompt(expr: str) -> str` (SQL: `btrim(regexp_replace(<expr>, <pattern literal>, '', 'g'), E' \t\r\n')`; the `E'...'` needs `\\t\\r\\n` escapes inside an f-string, as at `rollups.py:1609`); `_CLEAN_PROMPT = _clean_prompt("h.prompt_body")`.

- [ ] **Step 1: Write failing unit tests** in `test_rollups.py`: `_PROMPT_OK` contains `regexp_replace` and every sentinel test applies to `_CLEAN_PROMPT` (no bare `btrim(h.prompt_body)`); the `session_dims` upsert in `RECOMPUTE` aggregates the cleaned expression; `_earliest_merged("first_prompt")` wraps both sides in `nullif(..., '')` while other columns are unchanged; `_IS_AUTO_PROMPT` text contains no `regexp_replace` (turns unchanged).
- [ ] **Step 2: Write failing live tests** in `test_live_first_prompt.py` (model on `test_live_turns_daily._live`, `_READ_KEYS`, and `/tmp/repro-16528/test_repro_16528.py`, whose ingest/recompute/read helper to copy into the new file; events via `hook_event_builders.minimal(...) | {...}`; read `first_prompt` with `SELECT first_prompt FROM session_dims`):
  - `test_parity_sql_matches_python_over_the_corpus`: for each string in `PARITY_CORPUS`, `await conn.fetchval("SELECT " + rollups._clean_prompt("$1::text"), s)` equals `strip_pasted_content(s)`. Uses `_live()` with no sessions.
  - Parametrized rollup cases (typed, pasted-only, typed+pasted, mid-prompt, multiple blocks, unterminated, orphan, attribute-truncated) -> expected cleaned `first_prompt`.
  - Sentinel wrapped in a paste block is skipped, next prompt wins; empty-cleaned (`<pasted_content id="a">`) first prompt skipped, later prompt wins.
  - Recompute where the only earlier prompt cleans to empty keeps a previously stored `first_prompt`.
  - Old-CLI event (no `prompt_original`) and new-CLI event (extra `prompt_original`) both roll up.
- [ ] **Step 3:** Run unit file (fails), then live file with the explicit `TEST_LOCAL_PG_URL` (fails).
- [ ] **Step 4: Implement.** Add `value: str | None = None` to `_earliest` (the aggregated expression, default `h.<column>`; the `<> ''` filter stays on the raw column); rebuild `_PROMPT_OK` on `_CLEAN_PROMPT` (`<> ''`, lowercase `<> ALL(SENTINEL_EXACT)`, `starts_with` for TRIMMED and RAW prefixes); upsert uses `_earliest("prompt_body", _PROMPT_OK, value=_CLEAN_PROMPT)`; `_earliest_merged` applies `nullif(x, '')` to both operands when `column == "first_prompt"`.
- [ ] **Step 5:** Rerun unit and live files; expect PASS. Also run `tests/codemie/repository/cli_analytics/postgres/test_rollups.py` and `test_live_turns_daily.py` (live URL set) to confirm turn SQL is untouched.

Test-first: yes — cleaned-prompt assertions fail on the raw-markup `first_prompt`.

### Task 3: Alembic revision adding `session_dims.session_title`

**Files:**
- Create: `src/external/alembic_cli_analytics/versions/a1c1a0000007_cli_analytics_session_title.py` (`down_revision = "a1c1a0000006"`; model on `a1c1a0000006` header and `upgrade()/downgrade()` loop over `UPGRADE`/`DOWNGRADE` lists)
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_migrations.py:187-207` (`_declared_columns`)
- Modify: `src/codemie/repository/cli_analytics/postgres/rollups.py:796-810` (`_SESSION_DIMS_COLUMNS`: add `"session_title"`)

**Interfaces:**
- Produces: nullable `text` column `session_dims.session_title`; `UPGRADE = ["ALTER TABLE session_dims ADD COLUMN session_title text"]`, `DOWNGRADE = ["ALTER TABLE session_dims DROP COLUMN session_title"]`.

- [ ] **Step 1: Write failing tests** in `test_migrations.py`: load the new revision with `_load_revision`; revision/down_revision ids; statements are `ALTER TABLE ... ADD COLUMN` only (schema-only, no UPDATE); downgrade drops the column; `_declared_columns()` includes the new `UPGRADE` and `"session_title" in declared["session_dims"]`.
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_migrations.py -n 0 -q`; expect FAIL (no file).
- [ ] **Step 3:** Create the revision, extend `_declared_columns` to iterate the third revision's `UPGRADE`, add the column to `_SESSION_DIMS_COLUMNS` (this also keeps it out of `session_dims.attrs` via `_NOT_ATTRS`).
- [ ] **Step 4:** Rerun; expect PASS (live tests in later tasks migrate the throwaway DB via `PostgresAnalyticsRuntime.start()`).

Test-first: yes — the new-revision and declared-column tests fail before the file exists.

### Task 4: Fill `session_title` in the SESSION rollup

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/rollups.py:783` (`_COMMON_TEXT`: add `"session_title"`) and `:1312` (generic entry)
- Test: `test_rollups.py`, `test_live_first_prompt.py`

**Interfaces:**
- Consumes: column from T3. Produces: `session_dims.session_title` = latest non-empty value by (ts, ingest_seq), `left(nullif(btrim(lv.v->>'session_title', E' \t\r\n'), ''), 200)`.

- [ ] **Step 1: Write failing tests.** Unit: `("session_title", ...)` in `_SESSION_DIMS_VALUES` contains `left(` and `200`; `"session_title"` is in `_VALUE_KEYS`/`_NOT_ATTRS`. Live (`test_live_first_prompt.py`): `agent.session.start` with `session_title: "My name"` -> column set; second event with a later `session_title: "Renamed"` -> latest wins; 300-char name stored as 200 chars; name with surrounding whitespace/newlines trimmed; a recompute from events without a name keeps the stored value; latest whitespace-only title keeps the earlier real name (spec open risk); `session_dims.attrs` has no `session_title` key.
- [ ] **Step 2:** Run both (live URL set); expect FAIL.
- [ ] **Step 3:** Add `session_title` to `_COMMON_TEXT`; exclude it from the generic comprehension at `:1312` (as `platform`/`client_version`/`entrypoint` are) and add its own entry with the bound above.
- [ ] **Step 4:** Rerun; expect PASS, plus `test_migrations.py` (recompute columns declared).

Test-first: yes — the live `session_title` assertions fail while the column is never written.

### Task 5: Reader returns `session_title`; handler serves, searches, ranks

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:287` (add `coalesce(d.session_title, '') AS session_title` to the `sel` CTE), `:665` (`max(s.session_title) AS session_title`), `:728` (`nullif(d.session_title, '') AS session_title`)
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:725, 742-748, 871, 1054`
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (near `:382-418`), `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (use `make_handler`, `BASE_COST_ROW`, `make_detail_handler`, `make_filter`), `test_live_first_prompt.py`

**Interfaces:**
- Consumes: `strip_pasted_content` (T1), `session_title` column (T3/T4).
- Produces: reader rows keep `prompt` (raw first prompt) and gain `session_title`; handler helper `_served_prompt(title: object, first_prompt: object) -> str` = `_s(title).strip()` if non-empty else `strip_pasted_content(_s(first_prompt))`.

- [ ] **Step 1: Write failing tests.** Reader: the three queries select `session_title`. Handler (mocked rows): list and detail `prompt` is the title when set, else the cleaned first prompt (markup row from a pre-fix session served clean; markup-only row -> `""`); unnamed typed prompt unchanged; search matches a word of the name, a word of the cleaned first prompt of a named session, repository and branch, and does not match the word `pasted_content`; `get_efficiency` `worst_session_prompt` is the cleaned first prompt (truncated at `_PROMPT_PREVIEW_LEN`) even when `session_title` is set; `"first_prompt"`-like extra keys do not appear in session rows. Live: end-to-end via `PostgresCliAnalyticsReader` + `LocalAnalyticsHandler` (harness from `/tmp/repro-16528/test_repro_16528.py`, pricing patched as there): `get_sessions` and `get_session_detail` return clean `prompt` for typed, pasted-only, typed+pasted; named session returns its name in both; a stored row written with raw markup via direct `UPDATE session_dims` is served clean.
- [ ] **Step 2:** Run reader/handler tests and the live file; expect FAIL.
- [ ] **Step 3: Implement.** Add the three select columns; in `get_sessions` build each row's `prompt` with `_served_prompt(row.get("session_title"), row.get("prompt"))` and keep the cleaned first prompt in a local `dict[str, str]` keyed by session id for search only (not in the row, so the response is unchanged); search term matches served prompt, that dict value, repository, branch; detail uses `_served_prompt(meta.get("session_title"), meta.get("prompt"))`; efficiency uses `strip_pasted_content(_s(row.get("prompt"))) or None`.
- [ ] **Step 4:** Rerun the three test files plus `test_cli_analytics_handler_ordering.py`; expect PASS.

Test-first: yes — handler tests fail because `prompt` is served raw and the name is ignored.

### Task 6: Update the CLI Analytics guides

**Files:**
- Modify: `.ai-run/guides/integration/cli-analytics-events.md` (rows near `:57-58`, `session_dims` mapping `:66-70`), `.ai-run/guides/integration/cli-analytics-storage.md` (`session_dims` `:66`, merge kinds `:144-153`)

- [ ] **Step 1:** Edit the docs: prompt cleaning (tags removed, pasted text kept, trim set, sentinels on cleaned text, empty skipped); `session_title` -> `session_dims.session_title` (latest non-empty, trimmed, 200 chars, never in `attrs`) beside the unserved ai-title `title`; `prompt_original` is ignored (not read, served, searched, persisted); how `prompt` is chosen at read time (name, else cleaned first prompt) and that stored rows past raw retention are served clean by the Python net; the revision id `a1c1a0000007` where the storage guide lists revisions. Describe the code as it is, without history phrasing.
- [ ] **Step 2:** Run `make license-check` is not needed for `.md`; run `grep -n "session_title\|pasted_content" .ai-run/guides/integration/cli-analytics-*.md` and confirm both guides mention each term.

Test-first: no — documentation only; checked by the grep in Step 2.
