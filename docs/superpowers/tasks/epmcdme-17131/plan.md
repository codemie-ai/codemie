# EPMCDME-17131 Late diagram job must not overwrite a newer workflow save - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `WorkflowService.save_workflow_schema` writes only `schema_url`, and only when the stored `yaml_config` still equals the job's copy.

**Architecture:** Two new single-column methods on `WorkflowConfigRepository` (raw `text()` SQL, same style as `set_publish_state`), consumed by a guard in `save_workflow_schema` through a module-level repository instance. `WorkflowConfig.update()` (full-row merge) is no longer called by the diagram job.

**Tech Stack:** Python, SQLAlchemy `text()`, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/tasks/epmcdme-17131/implementation-brief.md`; research in `technical-analysis.md` (same dir).

## Global Constraints

- Commit per task using the repository's existing convention. No other git operations.
- No explanatory code comments. No `docs/<feature>.md` pages.
- Unit tests with mocks/patches only, matching neighbouring style. No real-Postgres tests.
- `set_schema_url` must not raise on `rowcount == 0` and must not touch `update_date`.
- `save_workflow_schema` must never call `WorkflowConfig.update()`; keep its swallow-and-log `try/except`.
- Line numbers below are current (brief has drifted ~2 lines).

## Verified in planning

- None vs "" `yaml_config`: `WorkflowConfig.yaml_config` is `Optional[str] = None` (`workflow_config.py:103`); `save()` (`base.py:514`) does no normalisation; Postgres stores NULL and "" distinctly. Create path queues the same object that `create_workflow` saved (`workflow.py:549` then `:559`), so plain `==` between stored and in-memory value is correct for None and "". No coalescing. Task 2 pins this with tests.
- Import cycle: `WorkflowConfigRepository` imports only `codemie.core.exceptions` and `codemie.core.workflow_models.workflow_config`; `workflow_service.py` already imports both that module (`:43`) and `WorkflowMarketplaceService` (`:57`), which imports the repository at load (`workflow_marketplace_service.py:27`). No new edge, no cycle. Task 2 Step 4 re-checks by running the module's tests.

## Out of scope (mention in MR only)

Overlapping PUT/rollback lost-history race; ticket comment about all-column impact. The 21-save live run is a manual pre-MR check.

---

### Task 1: Repository methods `get_yaml_config` and `set_schema_url`

**Files:**
- Modify: `src/codemie/repository/workflow_config_repository.py` (add after `set_publish_state`, `:28-45`)
- Create: `tests/unit/repository/test_workflow_config_repository.py`

**Interfaces:**
- Produces: `WorkflowConfigRepository.get_yaml_config(self, workflow_id: str) -> tuple[bool, str | None]` - `(False, None)` when the row is missing, `(True, <column value>)` otherwise (value may be None).
- Produces: `WorkflowConfigRepository.set_schema_url(self, workflow_id: str, schema_url: str) -> None`.

Test-first: yes - `test_get_yaml_config_returns_missing_marker_when_row_absent` fails with AttributeError until the method exists.

- [ ] **Step 1: Write failing tests** in the new file, mock style of `tests/unit/service/workflow_config/test_workflow_marketplace_service.py`: patch `codemie.repository.workflow_config_repository.WorkflowConfig` and wire `get_engine.return_value.begin.return_value.__enter__.return_value` (or `.connect()` for the read) to a MagicMock connection. Cases:
  - `get_yaml_config`: row absent -> `(False, None)`; row `("a: 1",)` -> `(True, "a: 1")`; row `(None,)` -> `(True, None)`; executed SQL text selects only `yaml_config` and binds `wf_id`.
  - `set_schema_url`: executes one statement whose text contains `schema_url IS DISTINCT FROM :schema_url` and not `update_date`, bound `wf_id`/`schema_url` match the arguments; with `rowcount = 0` it returns None and raises nothing.
- [ ] **Step 2:** Run `poetry run pytest tests/unit/repository/test_workflow_config_repository.py -v`. Expected: FAIL (AttributeError).
- [ ] **Step 3: Implement both methods** in `WorkflowConfigRepository`. `get_yaml_config`: `SELECT yaml_config FROM workflows WHERE id = :wf_id` with bound param, `.first()`, map to the tuple. `set_schema_url`: `UPDATE workflows SET schema_url = :schema_url WHERE id = :wf_id AND schema_url IS DISTINCT FROM :schema_url` inside `WorkflowConfig.get_engine().begin()`, no rowcount check. Docstrings one line, no comments.
- [ ] **Step 4:** Re-run the command from Step 2. Expected: PASS. Also run `poetry run pytest tests/unit/service/workflow_config/test_workflow_marketplace_service.py -q` (unchanged methods still fine).
- [ ] **Step 5:** Commit.

### Task 2: Guard and targeted write in `save_workflow_schema`, tests rewritten

**Files:**
- Modify: `src/codemie/service/workflow_service.py` (import next to `:57`; module-level `_workflow_config_repository = WorkflowConfigRepository()` next to `_marketplace_service` at `:62`; body of `save_workflow_schema` at `:802-817`)
- Modify: `tests/codemie/service/test_workflow_service.py` (rewrite tests at `:1331`, `:1474`, `:1516`; add new cases after `:1347`)

**Interfaces:**
- Consumes: `get_yaml_config`, `set_schema_url` from Task 1, reached via module-level `_workflow_config_repository` (tests patch `codemie.service.workflow_service._workflow_config_repository`).

Test-first: yes - `test_save_workflow_schema_stale_job_writes_nothing` fails because the current code writes the file and calls `update()` regardless of stored YAML.

- [ ] **Step 1: Rewrite the three existing tests** so they patch `_workflow_config_repository` (`get_yaml_config.return_value = (True, <yaml equal to workflow_config.yaml_config>)`) and assert `set_schema_url.assert_called_once_with(<id>, <encoded url>)` instead of `update` being called for the schema step. In `test_update_then_save_schema_prepends_history_then_persists_schema`, `mock_update.call_count` becomes 1 (history call only; stored yaml is `"old: changed"` after `_update_workflow_values`), keep the history assertions and rename the final assertions accordingly. In `test_update_then_save_schema_preserves_history`, `mock_update.assert_called_once_with()` becomes `mock_update.assert_not_called()` after `reset_mock`.
- [ ] **Step 2: Add new tests** (all with the repository patched, `FileRepositoryFactory` patched as in the existing test, `_stub_update` on the config):
  - `test_save_workflow_schema_stale_job_writes_nothing`: copy YAML "A", stored `(True, "B")` -> `write_file` not called, `set_schema_url` not called, stub `update` not called, `schema_url` unchanged.
  - `test_save_workflow_schema_current_job_writes_diagram`: stored equals copy -> `write_file` called once, `set_schema_url` called with `(id, "https://example.com/wf.svg")`.
  - `test_save_workflow_schema_workflow_deleted_mid_job`: `(False, None)` -> nothing written, no exception.
  - `test_save_workflow_schema_no_diagram_skips_db` (parametrize `None`, `b""`): `get_yaml_config` not called, nothing written.
  - `test_save_workflow_schema_never_calls_update`: stub `update` and also patch `WorkflowConfig.update`/`session.merge` path; assert not called in both the stale and current cases.
  - `test_save_workflow_schema_yaml_none_and_empty_handling` (parametrize): copy `None` vs stored `(True, None)` -> written; copy `""` vs stored `(True, "")` -> written; copy `None` vs `(True, "")` -> skipped.
  - `test_save_workflow_schema_swallows_repository_error`: `get_yaml_config.side_effect = Exception` -> no raise, nothing written.
  - Optional `test_history_sequence_with_late_schema_jobs`: loop `_update_workflow_values` over several YAMLs with a stale-copy `save_workflow_schema` between saves (stored YAML mocked to the newest); assert history lists each previous YAML once, in order.
- [ ] **Step 3:** Run `poetry run pytest tests/codemie/service/test_workflow_service.py -k "schema" -v`. Expected: new and rewritten tests FAIL.
- [ ] **Step 4: Implement** in `save_workflow_schema(self, workflow_config, workflow_schema)`: inside the existing `try`, `if not workflow_schema: return`; `exists, stored_yaml = _workflow_config_repository.get_yaml_config(workflow_config.id)`; if `not exists or stored_yaml != workflow_config.yaml_config`, `logger.info` a skipped-message with the workflow id and return; otherwise keep the existing `write_file` / `to_encoded_url` lines, then `_workflow_config_repository.set_schema_url(workflow_config.id, workflow_config.schema_url)` in place of `workflow_config.update()`. Keep the `except Exception` log unchanged.
- [ ] **Step 5:** Run `poetry run pytest tests/codemie/service/test_workflow_service.py tests/codemie/rest_api/routers/test_workflow.py tests/codemie/service/test_workflow_version_service.py tests/unit/service/workflow_config -q`. Expected: PASS (import-cycle check included, router tests unaffected).
- [ ] **Step 6:** Commit.
