# CHECKPOINT: fix_pure_chat_agents

## Items
1. `session_derived.py`: DONE. Added `_AGENTS_OBJECT`; `SKILL_NAMES_SQL` renamed `CLASSIFIER_NAMES_SQL`, unions the keys of
   `sd.agents` into both branches (summary branch now `FROM (SELECT s.name ... UNION SELECT a.name ...) x WHERE x.name <> ''`,
   the session_skills branch gets a third UNION member). `ORDER BY ... COLLATE "C"` and NULL-when-empty kept. Importers updated:
   `reader.py` (import, `get_skill_names_by_session` body and docstring), `session_derived._READ`. The port method name and the
   `skill_names` output key are unchanged; handler untouched. Leftover grep for `SKILL_NAMES_SQL` in src/tests/.ai-run: none.
2. `_framework()` guard: unchanged (kept, as briefed).
3. `delivery_framework.py`: docstring of `classify_delivery_framework` now says the input is skill names and subagent types;
   dropped `agent_types` from the "future signal types" sentence, since it is no longer future. Signature unchanged.
4. Out-of-scope items untouched (ES usage_attribution, `--now` backfill, Python-paged framework filter).

## Tests
- `test_session_derived.py`: updated `test_without_a_summary_the_classifier_reads_the_skills_of_otel_and_of_hooks`; added
  `test_the_classifier_reads_the_subagent_types_in_both_branches` (pins `_AGENTS_OBJECT` guard text, i.e. a non-object `agents`
  yields no keys, and the agent-keys member in both branches). Fails without the change: `_AGENTS_OBJECT` does not exist (and
  the SQL has no `a.name` member).
- `test_reader.py`: `test_skill_names_read_session_dims_with_the_sql_of_the_classifier` follows the renamed constant and pins the
  agents union in both branches.
- `test_cli_analytics_handler.py`: added `test_get_sessions_classifies_a_session_that_only_dispatched_a_factory_subagent` and
  `test_session_detail_classifies_a_session_that_only_dispatched_a_factory_subagent` (reader returns only
  `sdlc-factory:complexity-assessor`, label "CodeMie AI Factory"). Note: these pass on old handler code too, because the handler
  is name-agnostic by design; they pin that the label depends on the name alone. The behavior change itself is pinned by the SQL
  tests and the live test.
- `test_delivery_framework.py`: added `test_a_subagent_type_classifies_by_the_same_rule_as_a_skill_name` and parametrized
  `test_a_builtin_subagent_type_stays_pure_chat` (Explore, general-purpose, Plan). Same note: pins the rule contract.
- Live: `test_live_dispatch_gate.py::test_live_a_session_that_only_dispatches_a_factory_subagent_is_a_factory_session` (hook
  `agent.subagent.start`, `agent_type=sdlc-factory:complexity-assessor`, `skill_name` NULL, one api_request log; refresher run
  with `classify=classify_delivery_framework`; asserts stored column, list label and detail label). Deviation: `delivery_framework`
  is a TEXT column and `_session_dims` json-decodes, so the test reads it with a direct `fetchval`. **SKIPPED in this run**:
  `TEST_LOCAL_PG_URL` is not set (the dev `postgres` container is shared, not disposable, and the live refresh recomputes its
  whole queue, so I did not point the tests at it). It collects and imports fine. Not run: please run it against a disposable DB.
  Instead I validated `CLASSIFIER_NAMES_SQL` read-only (SELECT only, no writes) against the dev container's
  `codemie_analytics.session_dims`: the statement parses and runs, and `jsonb_object_keys(CASE ... END)` on a non-object gives no
  rows and no error.

## Docs
`.ai-run/guides/integration/cli-analytics-storage.md`: the `delivery_framework` derivation row (classifier reads skill names and
subagent types, `session_dims.agents`; rule-collision effect and built-ins in one line; label updates on next recompute) and the
"rollup-derived" row of the write-rules table.

## Gates (what ran)
- `poetry run pytest` on test_session_derived.py, test_reader.py, test_cli_analytics_handler.py, test_delivery_framework.py,
  test_live_dispatch_gate.py: 367 passed, 99 skipped (the skips are the live tests, no `TEST_LOCAL_PG_URL`).
  Before: not run separately; the full suite was not run (brief: narrow suites only).
- `make ruff`: format unchanged, `ruff check` passed.

## Not done
- Live test not executed (see above).
- Noticed, not changed: docstrings in `rollups.py` (~2164) and `ports.py` (~58) still say "skill names" for the classifier input.
