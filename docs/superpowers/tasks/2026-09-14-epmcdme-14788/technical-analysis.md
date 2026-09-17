# Technical Research

**Task**: ado azure-devops work-item json-parsing
**Generated**: 2026-09-14T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

ADO create and update work item tools fail when parsing nested work_item_json payload with JSON delimiter error. Bug in Azure DevOps create_work_item and update work item tools. The tools fail during parsing of the work_item_json argument. Error: 'Issues during attempt to parse work_item_json: Expecting comma delimiter: line 1 column 2829 (char 2828)'. Root cause area: serialization or escaping of nested JSON passed as a string, especially when the payload contains HTML content, quotes, punctuation, long text fields, and acceptance criteria. Acceptance criteria: (1) ADO create work item tool correctly parses valid nested work_item_json payloads with HTML-formatted descriptions and acceptance criteria. (2) ADO update work item tool correctly parses valid nested work_item_json payloads. (3) Tools succeed for payloads with long text, escaped quotes, punctuation, HTML tags, tags, and path fields. (4) No false-positive comma-delimiter errors for valid payloads. (5) Malformed JSON returns actionable error messages. (6) Regression tests for both create and update with simple, HTML-heavy, and nested-JSON payloads.

---

## 2. Codebase Findings

### Existing Implementations

- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/src/codemie_tools/azure_devops/work_item/tools.py` — Contains `BaseAzureDevOpsWorkItemTool`, `CreateWorkItemTool`, `UpdateWorkItemTool`, and eight other tool classes. The method `_transform_work_item` at line 130–139 is the single parsing site for `work_item_json` used by both create and update tools.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/src/codemie_tools/azure_devops/work_item/models.py` — Pydantic models: `AzureDevOpsWorkItemConfig`, `CreateWorkItemInput`, `UpdateWorkItemInput`. Both input models declare `work_item_json: str` with no pre-validation or sanitization.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/src/codemie_tools/azure_devops/work_item/toolkit.py` — `AzureDevOpsWorkItemToolkit` / `AzureDevOpsWorkItemToolkitUI` registering all 11 tools.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/src/codemie_tools/azure_devops/work_item/tools_vars.py` — `ToolMetadata` for `CREATE_WORK_ITEM_TOOL` and `UPDATE_WORK_ITEM_TOOL` with docstrings describing the `work_item_json` schema.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/src/codemie_tools/base/codemie_tool.py` — `CodeMieTool` base class: `_run` delegates to `execute`, wraps all exceptions in `ToolException`. `_parse_input` at line 57–75 also catches parse errors at the LangChain schema-parsing stage (distinct from the `work_item_json` string parsing done inside `execute`).

**Exact parsing logic (`_transform_work_item`, lines 130–139):**
```python
def _transform_work_item(self, work_item_json: str) -> list[dict]:
    try:
        params = json.loads(work_item_json)
    except ValueError as e:
        raise ToolException(f"Issues during attempt to parse work_item_json: {str(e)}")

    if "fields" not in params:
        raise ToolException("The 'fields' property is missing from the work_item_json.")

    return [{"op": "add", "path": f"/fields/{field}", "value": value} for field, value in params["fields"].items()]
```

The method uses a bare `json.loads` with no pre-processing, normalization, or LLM-output-aware repair step.

**Error double-wrapping in `CreateWorkItemTool.execute` (line 254–256) and `UpdateWorkItemTool.execute` (line 288–290):** both catch the re-raised `ToolException` from `_transform_work_item` and wrap it in a second `ToolException`. This means the final error message reads `"Issues during attempt to parse work_item_json: Issues during attempt to parse work_item_json: Expecting comma delimiter …"` — a double-wrap redundancy.

### Architecture and Layers Affected

- **Tool layer** (`src/codemie_tools/azure_devops/work_item/tools.py`): `_transform_work_item` in `BaseAzureDevOpsWorkItemTool`, consumed by `CreateWorkItemTool.execute` and `UpdateWorkItemTool.execute`.
- **Model/schema layer** (`src/codemie_tools/azure_devops/work_item/models.py`): `CreateWorkItemInput.work_item_json` and `UpdateWorkItemInput.work_item_json` — both are unvalidated `str` fields.
- **Base tool layer** (`src/codemie_tools/base/codemie_tool.py`): The `_parse_input` override and `_run` wrapper are upstream of `execute` and do not interfere with the problem, but the error-bubbling chain matters for diagnosis.

### Integration Points

- `azure-devops ^7.1.0b4` SDK — `WorkItemTrackingClient.create_work_item` and `update_work_item` consume the `patch_document` list produced by `_transform_work_item`. The SDK does no JSON parsing of field values; field values are passed as Python objects.
- `langchain_core.tools.ToolException` — The project-wide exception type raised and handled by the LangChain/LangGraph agent runtime. Both tools re-raise as `ToolException`.
- `CodeMieTool._post_process_output_content` (line 204–218) — serializes the result to JSON string using `json.dumps` on the way out; not implicated in the parse failure.

### Patterns and Conventions

- All ADO work item tools inherit from `BaseAzureDevOpsWorkItemTool` → `CodeMieTool` → `BaseTool`. New parsing logic must live in `_transform_work_item` or a helper called from it.
- Companion test_plan tools (`src/codemie_tools/azure_devops/test_plan/tools.py`) use the same `json.loads(param_str)` pattern without any sanitization — the same class of bug is latent there.
- Error messages must be actionable and distinct per failure mode (per error-handling guide): the current double-wrap breaks this.
- `ToolException` is the correct exception type for tool-layer errors; `ValueError` / `json.JSONDecodeError` are not user-facing.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/.ai-run/guides/agents/custom-tool-creation.md` — Instructs keeping tool logic in `codemie_tools`, adding focused tests when schema or serialization changes.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/.ai-run/guides/agents/agent-tools.md` — Tool schema must stay LangChain/LangGraph-compatible; complex unserialized objects must not be returned.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/.ai-run/guides/development/error-handling.md` — Each distinct failure mode must have its own error message. Avoid swallowing or double-wrapping exceptions.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/.ai-run/guides/testing/testing-patterns.md` — Tests mirror `src/` under `tests/codemie_tools/`. Mock provider boundaries. Seam tests required per callsite branch.

### Architectural Decisions

No ADR or recorded decision specific to `work_item_json` parsing was found. The choice to accept `work_item_json` as a raw string (rather than a typed Pydantic model) was made when the tool was initially authored; it is a deliberate LLM-friendly input pattern to allow flexible field sets, but no sanitization layer was ever added.

### Derived Conventions

- JSON inputs passed as `str` arguments (rather than typed Pydantic sub-models) is an established pattern in this repo: `CreateTestPlanInput.test_plan_create_params`, `CreateTestSuiteInput.test_suite_create_params`, and `AddTestCaseInput.suite_test_case_create_update_parameters` all follow the same pattern with bare `json.loads`.
- The project does not use `json_repair`, `orjson`, or any LLM-output JSON-repair library anywhere in `src/`. Any repair logic would be a new dependency.
- Error messages from tool-layer failures are prefixed descriptively (`"Issues during attempt to …"`, `"Error creating …"`).

---

## 4. Testing Landscape

### Existing Coverage

- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/tests/codemie_tools/azure_devops/work_item/test_tools.py` — Covers `SearchWorkItemsTool`, `CreateWorkItemTool`, `UpdateWorkItemTool`, `GetWorkItemTool`, `CreateCommentTool`, `GetRelationTypesTool`, `RemoveWorkItemRelationTool`, `MoveWorkItemTool`, and `GetWorkItemAttachmentContentTool`.
  - `TestCreateWorkItemTool.test_create_work_item_success` (line 111–125): uses only `'{"fields": {"System.Title": "Test Item"}}'` — a minimal ASCII-only payload.
  - `TestUpdateWorkItemTool.test_update_work_item_success` (line 128–141): uses only `'{"fields": {"System.Title": "Updated Title"}}'` — a minimal ASCII-only payload.
  - No test for invalid/malformed JSON input.
  - No test for HTML-containing field values.
  - No test for long text, escaped quotes, punctuation, or nested JSON string values.
  - No test for the `_transform_work_item` helper in isolation.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/tests/codemie_tools/azure_devops/work_item/test_toolkit.py` — Smoke tests for toolkit registration only.
- `/Users/Ramazan_Ay/Work/codemie-dev/codemie/tests/codemie_tools/azure_devops/work_item/test_config_mapping.py` — Config aliasing and field mapping tests only.

### Testing Framework and Patterns

- **Framework**: pytest (declared in `pyproject.toml`; `pytest.ini` sets `testpaths = tests`, `pythonpath = src`).
- **Fixtures**: `mock_config` returns `AzureDevOpsWorkItemConfig`; `mock_client` returns `Mock()`; tool fixtures inject the mock client via the `_client` property setter.
- **Mocking**: `unittest.mock.Mock` and `MagicMock` for the ADO SDK client. `patch.object` for mixin methods. No integration tests against real ADO endpoints.
- **Pattern**: each tool class gets its own `Test<ToolName>Tool` class with a fixture for the tool instance.

### Coverage Gaps

- `_transform_work_item` has no dedicated unit tests — neither for valid inputs nor for invalid ones.
- No test exercises a `work_item_json` payload containing HTML tags (`<p>`, `<br/>`, `<ul><li>`), double-quote characters inside field values, single quotes, ampersands, or long text (>1000 chars).
- No test exercises a malformed `work_item_json` string and verifies the `ToolException` message is actionable.
- No test exercises the double-wrapping behavior in `CreateWorkItemTool.execute` lines 254–256 or `UpdateWorkItemTool.execute` lines 288–290.
- The `test_plan` tools have the same parsing pattern and no corresponding tests for complex payloads.

---

## 5. Configuration and Environment

### Environment Variables

- `AZURE_DEVOPS_CACHE_DIR` — Set to `""` at module import time if absent (line 63–64 of `tools.py`). No bearing on JSON parsing.
- No env vars specific to JSON parsing or field serialization were found.

### Configuration Files

- `AzureDevOpsWorkItemConfig` (models.py): `organization_url`, `project`, `token`, `limit`. No serialization or encoding configuration.
- `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`.

### Feature Flags and Deployment Concerns

No feature flags or deployment-level concerns for this domain were found. The tools are registered in `AzureDevOpsWorkItemToolkitUI` and loaded at agent startup; no hot-reload path exists.

---

## 6. Risk Indicators

- **Double exception wrap in CreateWorkItemTool and UpdateWorkItemTool**: `_transform_work_item` already raises `ToolException` when `json.loads` fails; the callers then catch any `Exception` (which includes `ToolException`) and wrap it again with a second `ToolException`. The agent receives a message of the form `"Issues during attempt to parse work_item_json: Issues during attempt to parse work_item_json: Expecting comma delimiter …"`. The outer catch at lines 254–256 and 288–290 is the immediate source of the reported error message duplication.
- **No pre-processing or normalization of LLM-generated JSON strings**: LLMs routinely produce strings with unescaped control characters, smart quotes (`"` / `"`), single-quoted keys, trailing commas, or newlines inside string values. Python's `json.loads` rejects all of these. The `work_item_json` parameter is LLM-generated and is therefore high-risk for malformed input.
- **HTML content and embedded JSON strings in field values**: fields like `System.Description` and acceptance-criteria fields often contain HTML (e.g. `<p>`, `<ul>`, `&lt;`). These are legal JSON string values only if the LLM correctly escapes them (e.g. `<` as `\u003c` is not required but `"` inside must be `\"`). Unescaped angle brackets alone are valid in JSON strings; the most likely failure mode is an unescaped double-quote inside an HTML attribute or an acceptance-criteria template that the LLM emits without escaping.
- **No tests for the main failure path**: the error surface (HTML payloads, long text, nested JSON) is entirely untested; any fix risks regression with no safety net.
- **Latent same bug in test_plan tools**: `CreateTestPlanTool`, `CreateTestSuiteTool`, and `AddTestCaseTool` all call `json.loads(param_str)` directly without sanitization; they would fail identically for complex payloads.
- **Error message actionability**: `json.JSONDecodeError` includes a position (`char 2828`), which is useful for debugging but not actionable for an LLM agent. The agent needs to know whether the payload was structurally invalid, or whether it just needs to escape certain characters.

---

## 7. Summary for Complexity Assessment

The bug is localized to a single private method, `_transform_work_item`, in `/Users/Ramazan_Ay/Work/codemie-dev/codemie/src/codemie_tools/azure_devops/work_item/tools.py` (lines 130–139). This method is called by exactly two tools — `CreateWorkItemTool` and `UpdateWorkItemTool`. The core change surface is small: one method, its two callers, one models file, and one test file. The double-wrap bug in the callers can be resolved by narrowing the caught exception type (catching `json.JSONDecodeError` or `ValueError` in `_transform_work_item` and not catching `ToolException` in the callers, or restructuring the try/except blocks). The deeper issue — LLM-generated JSON strings that contain unescaped characters — requires a decision about whether to add a lenient parse step (e.g. a `json_repair`-style fallback, or a targeted sanitization before `json.loads`) and what error message the agent should receive when the payload is genuinely malformed.

Test coverage for the exact failure scenario is completely absent: the two existing happy-path tests use trivial ASCII-only payloads. The acceptance criteria require six new test scenarios covering simple, HTML-heavy, nested-JSON, malformed-JSON, and edge-case payloads for both tools. The `_transform_work_item` helper should receive its own dedicated test class. No new dependencies, database models, migrations, or API routes are needed; this is entirely within the `codemie_tools` layer with no cross-service impact.

Complexity is low-to-medium: the fix is architecturally contained and the file change surface is one implementation file, one (optionally) models file, and one test file. The main risk is the design decision around lenient parsing — adding a repair library changes the dependency graph and may mask genuinely malformed LLM output in ways that are hard to test exhaustively. Keeping the fix to correct exception handling and clear error messages is lower risk; adding a lenient parse layer is higher risk but would satisfy acceptance criterion (3) more robustly.

---

## 8. External References

None named by the task.
