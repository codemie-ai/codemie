# Normalize CLI Analytics Model Name Prefixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `get_model_breakdown` (the one query backing `model_breakdown`, `cost_by_model`, `tokens_by_model`) group by a canonical model name, so `converse/`-routed, region/provider-prefixed, and Bedrock-version-suffixed variants of the same model stop fragmenting into separate rows (EPMCDME-15523).

**Architecture:** Single SQL-level fix in the PostgreSQL reader: add a small `_normalized_model_name()` SQL-fragment helper (same style as the file's existing `_scope`/`_sessions_cte` helpers) and apply it to the `SELECT`, `GROUP BY`, and `HAVING` of `get_model_breakdown` so `COUNT(DISTINCT session_id)` aggregates per canonical name, not per raw string. No handler-layer changes.

**Tech Stack:** Python, asyncpg, PostgreSQL (`regexp_replace`), pytest + `unittest.mock`.

**Spec:** Inline requirements (Jira EPMCDME-15523), reproduced in the task's technical analysis context — no `spec.md` exists for this task. Technical analysis: `docs/superpowers/tasks/2026-10-12-normalize-model-name-regional-prefixes/technical-analysis.md`.

## Global Constraints

- Fix is local to `src/codemie/repository/cli_analytics/` and `src/codemie/service/analytics/handlers/cli_analytics_handler.py` only, plus their tests — no other file changes.
- Do not touch `coding_agent_pricing.py`, `llm_pricing_validator.py`, or `llm_handler.py` — their normalizers are explicitly out of scope.
- Do not change `cost_daily.model_name` storage, ingestion, or rollups — it is documented as an intentional raw-OTel-name contract.
- Normalization happens inside the SQL `GROUP BY`/`SELECT`/`HAVING` — never as a Python/handler-level post-aggregation merge.
- Strip exactly three things, nothing else: the leading `converse/` routing prefix, an optional region token + `anthropic.` provider prefix immediately before it, and a trailing Bedrock version/inference-profile tag matching `-v\d+:\d+` (e.g. `-v1:0`) at the very end of the string. Everything else inside the model id — including date stamps (`-20251001`) and genuine version-number dashes (`claude-sonnet-5` vs `claude-sonnet-5-5`) — is left untouched, so those two stay distinct canonical names.
- Commit per task using the repository's existing convention (branch `EPMCDME-15523_...`, commit messages prefixed `EPMCDME-15523: ...`).

## Review Focus

- A raw name with no `anthropic.` substring and no trailing `-v\d+:\d+` tag (e.g. `claude-sonnet-5-5`) must pass through completely unchanged.
- A raw name with bare `anthropic.` and no region token (`anthropic.claude-haiku-4-5-20251001-v1:0`) must still have `anthropic.` stripped, not just the region-qualified form.
- The trailing-suffix strip must only match the literal `-v<digits>:<digits>` shape at the end of the string — it must not touch the date stamp (`-20251001`) or a model's own version dash (`claude-sonnet-5-5`'s trailing `-5`).
- The `HAVING ... <> ''` empty-name filter must apply to the *normalized* expression, so a name that normalizes to empty string is still excluded, not just a raw-empty one.
- `ORDER BY` must keep a deterministic tiebreak on the model name using the same `COLLATE "C"` as before, now against the output alias rather than `d.model_name` directly.
- All five of the ticket's literal Haiku examples — the bare name and the four prefixed/suffixed variants — must normalize to the exact same string; this is checked with a direct behavioral assertion, not just a SQL-shape string match.

## Acceptance Criteria

- [ ] `model_breakdown`, `cost_by_model`, and `tokens_by_model` each return at most one row per canonical model name per window; `converse/`-, region-, provider-, and Bedrock-suffix-prefixed/suffixed variants of the same model — including the already-bare raw name — merge into one row with correctly summed `session_count`/`cost_usd`/`total_tokens`.
- [ ] Distinct, non-prefix/suffix-variant models (e.g. `claude-sonnet-5` vs `claude-sonnet-5-5`) remain separate rows.
- [ ] `session_count` for a merged canonical model is a correct `COUNT(DISTINCT session_id)` across all its raw-name variants — computed in SQL, not Python.
- [ ] New test coverage exists for `get_model_breakdown`'s model-name normalization/grouping behavior, including a case where the bare raw name and all four prefixed/suffixed variants collapse to one canonical value.
- [ ] No changes outside `repository/cli_analytics/` and `service/analytics/handlers/cli_analytics_handler.py` (plus their tests).

---

### Task 1: Normalize model_name prefixes and Bedrock version suffix in `get_model_breakdown`'s SQL grouping

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:215-231` (the `get_model_breakdown` method); add the new helper near the file's other SQL-fragment helpers (e.g. beside `_scope`, around line 190).
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (new tests near the existing `get_model_breakdown` coverage, ~line 330-375).

**Interfaces:**
- Produces: `_normalized_model_name(column: str) -> str` — a pure string function returning the Postgres SQL expression that strips a leading `converse/` prefix, an optional `<region>.anthropic.`/`anthropic.` prefix, and a trailing `-v\d+:\d+` Bedrock version tag from the given column reference. No other task consumes it.

**Test-first: yes — three failing tests in `test_reader.py`:**
1. `test_normalized_model_name_sql_expression_is_pinned` — assert `_normalized_model_name("d.model_name")` returns the exact literal `regexp_replace(regexp_replace(regexp_replace(d.model_name, '^converse/', ''), '^([a-z]+\.)?anthropic\.', ''), '-v\d+:\d+$', '')`, so later tests can substring-match it.
2. `test_model_breakdown_groups_by_the_normalized_expression` — via the existing `_sql_of("get_model_breakdown")` helper, assert the pinned expression from test 1 appears in the `SELECT`'s `model_name` output, in `GROUP BY`, and in `HAVING` (not raw `d.model_name` in any of the three), and that `ORDER BY` ends with `model_name {_C}`.
3. `test_normalization_collapses_known_bug_variants_but_not_distinct_models` — a behavioral mirror check, independent of SQL execution:

```python
def _apply(raw: str) -> str:
    s = re.sub(r"^converse/", "", raw)
    s = re.sub(r"^([a-z]+\.)?anthropic\.", "", s)
    return re.sub(r"-v\d+:\d+$", "", s)

def test_normalization_collapses_known_bug_variants_but_not_distinct_models():
    variants = [
        "converse/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "converse/eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-haiku-4-5-20251001-v1:0",
        "claude-haiku-4-5-20251001",
    ]
    assert {_apply(v) for v in variants} == {"claude-haiku-4-5-20251001"}
    assert _apply("claude-sonnet-5") == "claude-sonnet-5"
    assert _apply("claude-sonnet-5-5") == "claude-sonnet-5-5"
    assert _apply("claude-sonnet-5") != _apply("claude-sonnet-5-5")
```

   (`_apply` mirrors the three `regexp_replace` calls pinned in test 1 using Python's equivalent regex syntax — it documents the intended runtime behavior that unit tests on generated SQL text alone cannot exercise, since these tests never run against a real Postgres. All five of the ticket's literal Haiku strings, including the already-bare raw name, must land on the identical canonical value; the two Sonnet names must stay distinct from each other and from themselves unmodified.)

All three must fail against current code: 1 and 2 with `ImportError`/`AttributeError` (`_normalized_model_name` doesn't exist, SQL still groups by raw `d.model_name`); test 3 is a standalone assertion of the intended contract and can be written and verified before the fix exists.

- [ ] Step 1: Add the three tests above to `test_reader.py`.
- [ ] Step 2: Run `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "normalized_model_name or model_breakdown or normalization_collapses" -v` from `codemie/`; confirm tests 1 and 2 fail as described (test 3 passes already since it only exercises its own inline mirror regex, not the production code — note that in the PR, but it still documents the contract test 2 will rely on being true in SQL).
- [ ] Step 3: Add the helper and rewire the query:

```python
def _normalized_model_name(column: str) -> str:
    """Strip a leading 'converse/' routing prefix, an optional region token plus
    'anthropic.' provider prefix (e.g. 'us.anthropic.', 'anthropic.'), and a
    trailing Bedrock version/inference-profile tag ('-v1:0'-style, '-v\\d+:\\d+').
    Never touches a date stamp or a model's own version dash inside the id.
    """
    without_converse = f"regexp_replace({column}, '^converse/', '')"
    without_prefix = f"regexp_replace({without_converse}, '^([a-z]+\\.)?anthropic\\.', '')"
    return f"regexp_replace({without_prefix}, '-v\\d+:\\d+$', '')"
```

  In `get_model_breakdown`, replace `d.model_name` with `_normalized_model_name("d.model_name")` in the `SELECT`'s `model_name AS model_name` column, in `GROUP BY`, and in `HAVING ... <> ''`; keep `ORDER BY session_count DESC, cost_usd DESC, model_name {_C}` referencing the output alias (unchanged).
- [ ] Step 4: Run the same pytest command as Step 2, plus the full file: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`; confirm all three new tests pass and no existing test (e.g. the parametrized SQL-shape tests around line 331) regressed.
- [ ] Step 5: Commit: `git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py && git commit -m "EPMCDME-15523: Normalize model_name routing/region/provider prefixes and Bedrock version suffix in get_model_breakdown"`.
