# Technical Research

**Task**: tool-configs pydantic json-schema file-analysis
**Generated**: 2026-10-19T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13966 (Bug, Major): FileAnalysisConfig is silently dropped from GET /v1/tools/configs

Summary
GET /v1/tools/configs logs an ERROR on every call and returns a partial configuration list. FileAnalysisConfig is omitted while the endpoint returns 200 OK.

Severity: Minor. Component: Backend (codemie) — codemie_tools. Affected version: main; reproduced at commit dec5b13dd.

Actual result
- An ERROR is logged on every request:
  Error getting info from FileAnalysisConfig: Cannot generate a JsonSchema for core_schema.CallableSchema
- An INFO message reports a partial result: Retrieved info for 29 tool configs, 1 failed
- The API responds with 200 OK, but filesanalysisconfig is absent.

Expected result
All registered tool configs are returned (expected: 30/30), and no ERROR is logged.

Steps to reproduce
1. Start the local stack. 2. Call GET /v1/tools/configs. 3. Inspect backend logs and the response.
Minimal direct reproduction:
  from codemie_tools.file_analysis.models import FileAnalysisConfig
  FileAnalysisConfig.model_json_schema()
  # raises PydanticInvalidForJsonSchema: Cannot generate a JsonSchema for core_schema.CallableSchema

Root cause
In src/codemie_tools/file_analysis/models.py, FileAnalysisConfig defines:
  chat_model: Optional[BaseChatModel] = Field(default=None, exclude=True)
src/codemie_tools/base/toolkit_provider.py calls config_class.model_json_schema() for every registered config class. BaseChatModel carries callable fields that cannot be rendered to JSON Schema. Field(exclude=True) excludes the field from serialization/model_dump but not from class JSON-schema generation.
The broad exception handling in _process_config_class logs the error and returns success=False. get_available_tools_configs_info() then omits the failed config while returning the remainder, resulting in a successful yet incomplete API response.

Impact
- ERROR-level log spam obscures genuine backend errors.
- FileAnalysisConfig is missing from the endpoint contract.
- Any future config containing a non-JSON-schema-serializable field can be silently omitted.
- The current UI consumer reportedly has no observed user-visible regression because it only extracts defaults/placeholders and this config provides no editable fields; future consumers remain at risk.

Suggested fix (requires verification)
Use Pydantic SkipJsonSchema for the runtime-only field while retaining exclude=True for serialization:
  from pydantic.json_schema import SkipJsonSchema
  chat_model: SkipJsonSchema[Optional[BaseChatModel]] = Field(default=None, exclude=True)

Acceptance criteria
- FileAnalysisConfig.model_json_schema() succeeds.
- GET /v1/tools/configs includes filesanalysisconfig.
- No schema-generation ERROR is emitted for FileAnalysisConfig.
- Add regression coverage for the model schema and config-discovery/API result.

Notes
The reported issue is not caused by branch EPMCDME-13212-file-datasource-upuches.

Already verified locally on origin/main 3826ec9b8 (pydantic 2.13.5): the minimal repro raises as described; get_available_tools_configs_info() returns 29 of 30 registered config classes, filesanalysisconfig missing; a copy of the class with the SkipJsonSchema fix generates a schema successfully and omits chat_model from properties. The endpoint is src/codemie/rest_api/routers/tool.py (GET /tools/configs, line ~47-57).

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie_tools/file_analysis/models.py:41` — `FileAnalysisConfig(CodeMieToolConfig, FileConfigMixin)`; fields `chat_model: Optional[BaseChatModel] = Field(default=None, exclude=True)` and `preconverted_content: dict[str, str] = Field(default_factory=dict)`. Imports only `Field` from pydantic.
- `src/codemie_tools/base/models.py:205-208` — `FileConfigMixin.input_files: Optional[List[Any]] = Field(default=None, exclude=True)` (`Any` renders to JSON Schema fine; also present in schema output).
- `src/codemie_tools/base/toolkit_provider.py`:
  - `_process_config_class` (~L416-446): `config_name = config_class.__name__.lower()`, calls `config_class.model_json_schema()`, maps `properties` through `_extract_field_data`; `except Exception` → `logger.error(f"Error getting info from {name}: {e}")`, returns `(None, False)`.
  - `get_available_tools_configs_info` (~L449-490): `@functools.lru_cache(maxsize=None)`; drops failures, logs `Retrieved info for N tool configs, M failed` at INFO.
  - `_extract_field_data` / `_determine_field_type` (L356-413) post-process schema properties.
- `src/codemie/rest_api/routers/tool.py:46-63` — `GET /tools/configs`, `response_model=List[Dict[str, Any]]`, returns `toolkit_provider.get_available_tools_configs_info()`; 500 only if the provider raises.
- Runtime constructors passing `chat_model`: `src/codemie_tools/file_analysis/toolkit.py:101,121`, `src/codemie/service/tools/toolkit_service.py:1343` (`FileAnalysisConfig(input_files=[], chat_model=llm)`).

### Architecture and Layers Affected
- Tool model layer (`codemie_tools/file_analysis/models.py`) — the defect site.
- Tool discovery/provider layer (`codemie_tools/base/toolkit_provider.py`) — consumes the schema; swallows errors.
- REST router (`rest_api/routers/tool.py`) — passthrough, no change in behavior expected.

### Integration Points
- `toolkit_provider` discovers config classes by recursive module scan (`_find_tool_configs`, `is_config_class`), so the class is registered automatically.
- Consumer: `codemie-ui` reads defaults/placeholders from this endpoint (per ticket). The response model is untyped dict, so `tests/codemie/rest_api/router_guards/baselines/openapi.json` is not affected by the payload content.

### Patterns and Conventions
- `Field(exclude=True)` is the widespread convention for runtime-only fields across `codemie_tools` (jira, confluence, gitlab, workspace tools, etc.). No `SkipJsonSchema` usage exists anywhere in `src/` or `tests/`.
- Other files hold `BaseChatModel` fields, but on toolkits/tools (e.g. `file_analysis/toolkit.py`, `git/toolkit.py`, `data_management/file_system/toolkit.py`), not on `CodeMieToolConfig` subclasses; `FileAnalysisConfig` is the only config class among the discovered 30 that fails.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/testing/testing-patterns.md`, `testing-service-patterns.md`, `testing-api-patterns.md`, `local-verification.md`; `.ai-run/guides/standards/code-quality.md`; `.ai-run/guides/quality-gates.md`. None covers tool-config schema discovery specifically.

### Architectural Decisions
- None recorded for tool-config schema generation. CHANGELOG has no mention of FileAnalysisConfig or `/tools/configs`.

### Derived Conventions
- Provider functions are `lru_cache`d; tests must clear caches (an autouse fixture already does).
- Error handling in provider is log-and-skip per item (also used in `get_tools`, `get_available_toolkits_info`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/base/test_toolkit_provider.py` — `clear_lru_caches` autouse fixture (L37-55); `test_get_available_tools_configs_info` (L196, mocked config class); `test_integration_get_available_tools_configs_info` (L441, real discovery, but only asserts `len >= 0` and shape — it passes despite the bug).
- `tests/codemie/rest_api/routers/test_tool.py:~100-140` — `/v1/tools/configs` success, empty, error cases; provider fully mocked.
- `tests/codemie_tools/file_analysis/test_file_analysis.py` — constructs `FileAnalysisConfig(input_files=[...])`; no schema tests.

### Testing Framework and Patterns
- pytest 8, pytest-mock, pytest-asyncio, pytest-xdist (`pytest.ini`); `unittest.mock.patch` of `toolkit_provider` in router tests; FastAPI TestClient with `mock_auth_header`/`mock_authenticate` fixtures.

### Coverage Gaps
- No test calls `FileAnalysisConfig.model_json_schema()`.
- No test asserts every discovered config class produces info (failed count == 0) or that `fileanalysisconfig` is present.
- No test asserts `chat_model` stays excluded from `model_dump()` while instances still accept a `BaseChatModel`.

---

## 5. Configuration and Environment

### Environment Variables
- None relevant.

### Configuration Files
- `pyproject.toml:132` `pydantic = "^2.9.2"`; `poetry.lock` pins pydantic 2.13.5. `langchain-core = "1.3.3"`. `pydantic.json_schema.SkipJsonSchema` exists in pydantic >= 2.1, so it is available under the declared range.

### Feature Flags and Deployment Concerns
- None.

---

## 6. Risk Indicators

- Key name mismatch: provider emits `config_class.__name__.lower()` = `fileanalysisconfig`; the ticket text says `filesanalysisconfig`. Tests/acceptance should use `fileanalysisconfig`.
- `_process_config_class` broad `except Exception` + INFO-level summary means any future non-serializable config field fails silently again; the existing integration test (`len >= 0`) would not catch it.
- Speculative: wrapping with `SkipJsonSchema[Optional[BaseChatModel]]` must keep validation/assignment of a real `BaseChatModel` (`toolkit.py:101,121`, `toolkit_service.py:1343`) and `exclude=True` dump behavior unchanged — worth a regression assertion.
- Speculative: once fixed, `fileanalysisconfig` will expose `input_files` and `preconverted_content` in the endpoint payload; the UI consumer reportedly tolerates this but it is a contract addition.
- `lru_cache` on discovery: tests that import real configs must rely on the existing cache-clear fixture.

---

## 7. Summary for Complexity Assessment

The defect is confined to one field declaration in `src/codemie_tools/file_analysis/models.py` (`chat_model: Optional[BaseChatModel] = Field(default=None, exclude=True)`), which makes `model_json_schema()` raise; `src/codemie_tools/base/toolkit_provider.py::_process_config_class` catches it, logs ERROR and drops the class, so `GET /v1/tools/configs` (`src/codemie/rest_api/routers/tool.py`) returns 29/30 configs with 200. Layers touched: tool model (fix site), with the provider and router only as verification surfaces. Change surface is one source file plus tests.

Technical novelty is low: `SkipJsonSchema` is standard pydantic (installed 2.13.5) though not yet used in this repo. Callers that pass `chat_model=` at runtime (`file_analysis/toolkit.py`, `service/tools/toolkit_service.py`) must keep working.

Test posture: provider and router tests exist but mock the config classes; the real-discovery integration test only asserts `len >= 0`, so it misses this bug. Regression tests belong in `tests/codemie_tools/file_analysis/` (schema succeeds, `chat_model` absent from properties) and `tests/codemie_tools/base/test_toolkit_provider.py` (real discovery includes `fileanalysisconfig` / no failures). Main risks are the ticket's `filesanalysisconfig` typo vs actual key `fileanalysisconfig`, and the silent-skip pattern remaining for future configs.

---

## 8. External References

None named by the task.
