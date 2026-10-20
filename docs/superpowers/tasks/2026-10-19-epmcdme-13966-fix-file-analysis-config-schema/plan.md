# EPMCDME-13966 FileAnalysisConfig Schema Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `FileAnalysisConfig.model_json_schema()` succeed so `GET /v1/tools/configs` returns `fileanalysisconfig` with no ERROR log.

**Architecture:** Wrap the runtime-only `chat_model` field in pydantic `SkipJsonSchema`, keeping `default=None, exclude=True`. Provider, router and other configs are untouched. Regression tests pin the model schema and real config discovery.

**Tech Stack:** Python, pydantic 2.13.5 (`pydantic.json_schema.SkipJsonSchema`), langchain-core, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-19-epmcdme-13966-fix-file-analysis-config-schema/spec.md`

Commit per task using the repository's existing convention (`EPMCDME-13966: Capitalised summary`).

## Global Constraints

- No dependency change (`pydantic = "^2.9.2"` already has `SkipJsonSchema`).
- Endpoint key is `fileanalysisconfig`, not the ticket's `filesanalysisconfig`.
- Never assert a hardcoded total config count (no "30"); assert presence and zero failures.
- Do not touch `_process_config_class` error handling, log levels, router, response model, OpenAPI baseline, or other `Field(exclude=True)` fields.

## Review Focus

- `chat_model=<BaseChatModel instance>` must still validate and remain on the instance (`SkipJsonSchema` must not alter validation) — pinned in Task 1.
- `model_dump()` must still omit `chat_model` — pinned in Task 1.
- `input_files` / `preconverted_content` appearing in the schema is expected, not a regression.
- Discovery results are `lru_cache`d; tests rely on the autouse `clear_lru_caches` fixture (`tests/codemie_tools/base/test_toolkit_provider.py:36-55`).

---

### Task 1: Exclude `chat_model` from JSON schema

**Files:**
- Modify: `src/codemie_tools/file_analysis/models.py:20,41`
- Test: `tests/codemie_tools/file_analysis/test_file_analysis_config_schema.py` (create)

**Test-first:** yes — `FileAnalysisConfig.model_json_schema()` raises `PydanticInvalidForJsonSchema` today.

- [ ] **Step 1: Write the failing tests**

```python
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from codemie_tools.file_analysis.models import FileAnalysisConfig

def test_model_json_schema_skips_chat_model():
    schema = FileAnalysisConfig.model_json_schema()
    assert "chat_model" not in schema.get("properties", {})

def test_chat_model_accepted_and_excluded_from_dump():
    llm = FakeListChatModel(responses=["ok"])
    config = FileAnalysisConfig(input_files=[], chat_model=llm)
    assert config.chat_model is llm
    assert "chat_model" not in config.model_dump()
```

- [ ] **Step 2: Run** `poetry run pytest tests/codemie_tools/file_analysis/test_file_analysis_config_schema.py -v` — expect the schema test to FAIL with `PydanticInvalidForJsonSchema` (the runtime test passes already; it guards the change).
- [ ] **Step 3: Implement.** In `models.py` add `from pydantic.json_schema import SkipJsonSchema` and change line 41 to `chat_model: SkipJsonSchema[Optional[BaseChatModel]] = Field(default=None, exclude=True)`. Leave the docstring as is.
- [ ] **Step 4: Run** the same command — expect both PASS.

### Task 2: Real-discovery regression in toolkit provider tests

**Files:**
- Modify: `tests/codemie_tools/base/test_toolkit_provider.py` (add a test after `test_integration_get_available_tools_configs_info`, L441-462; leave that test unchanged)

**Interfaces:**
- Consumes: Task 1's fixed `FileAnalysisConfig`; `get_available_tools_configs()` and `get_available_tools_configs_info()` (already imported, L21-33). The provider uses stdlib `logging`, so `caplog` captures it.

**Test-first:** yes — real discovery currently omits `fileanalysisconfig` and logs `Error getting info from FileAnalysisConfig` (fails if run before Task 1).

- [ ] **Step 1: Write the test**

```python
def test_integration_all_tool_configs_produce_info(caplog):
    caplog.set_level(logging.INFO, logger="codemie_tools.base.toolkit_provider")
    config_classes = get_available_tools_configs()
    configs_info = get_available_tools_configs_info()
    keys = {next(iter(item)) for item in configs_info}
    assert "fileanalysisconfig" in keys
    assert len(configs_info) == len(config_classes)
    assert not [r for r in caplog.records if r.getMessage().startswith("Error getting info from")]
    assert any("0 failed" in r.getMessage() for r in caplog.records)
```

Add `import logging` to the module imports.

- [ ] **Step 2: Run** `poetry run pytest tests/codemie_tools/base/test_toolkit_provider.py -k all_tool_configs_produce_info -v` — expect PASS with Task 1 applied (verify FAIL by temporarily stashing Task 1's `models.py` change only if executing out of order).

## Negative-constraint pass

- Non-goal "no change to `_process_config_class` / log levels / endpoint failing" — no task edits `toolkit_provider.py` or `tool.py`.
- Non-goal "no migration of other `Field(exclude=True)` fields" — Task 1 edits only `chat_model`.
- Non-goal "no response shape / OpenAPI baseline change" — no router or baseline edits.
- Non-goal "do not hide `input_files` / `preconverted_content`" — Task 1 asserts nothing about them and wraps only `chat_model`.
- Non-goal "no codemie-ui / codemie-sdk change" — all files are in `codemie/`.
- Constraint "no hardcoded config count" — Task 2 compares discovered vs returned counts.
