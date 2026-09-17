# EPMCDME-14788: Fix ADO Work Item JSON Parsing Bug Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix double-exception-wrap and improve error messages in `_transform_work_item` so ADO create/update work item tools handle valid HTML-rich JSON payloads and return actionable errors for genuinely malformed input.

**Architecture:** All changes are confined to the `codemie_tools` layer. `_transform_work_item` in `BaseAzureDevOpsWorkItemTool` is the single parsing site; callers `CreateWorkItemTool.execute` and `UpdateWorkItemTool.execute` are fixed to stop re-wrapping the already-typed `ToolException`. No new dependencies are added.

**Tech Stack:** Python 3.x, `json` stdlib, `langchain_core.tools.ToolException`, pytest.

Commit per task using the repository's existing convention.

## Acceptance criteria

- ADO create work item tool correctly parses valid nested `work_item_json` with HTML-formatted descriptions and acceptance criteria.
- ADO update work item tool correctly parses valid nested `work_item_json` with HTML-formatted descriptions and acceptance criteria.
- Tools succeed for payloads with long text, escaped quotes, punctuation, HTML tags, tags, and path fields.
- No false-positive comma-delimiter errors for valid payloads.
- Malformed JSON returns actionable error messages identifying the location and what to fix.
- Regression tests exist for both create and update covering: simple payloads, long HTML descriptions, acceptance criteria fields, tags/path fields, and nested JSON string input.
- No regression for existing ADO create/update scenarios.

---

## File map

| Action | Path |
|--------|------|
| Modify | `src/codemie_tools/azure_devops/work_item/tools.py:130-139, 254-256, 288-290` |
| Modify | `tests/codemie_tools/azure_devops/work_item/test_tools.py` |

---

### Task 1: Fix `_transform_work_item` error message and remove double-wrap in callers

**Files:**
- Modify: `src/codemie_tools/azure_devops/work_item/tools.py:130-139, 254-256, 288-290`

**Test-first: yes** — failing test that asserts `ToolException` is not double-wrapped and message is actionable.

- [ ] **Step 1: Write the failing tests**

Add a new `TestTransformWorkItem` class in
`tests/codemie_tools/azure_devops/work_item/test_tools.py` (before `TestSearchWorkItemsTool`):

```python
import json
from langchain_core.tools import ToolException
import pytest
from codemie_tools.azure_devops.work_item.tools import BaseAzureDevOpsWorkItemTool
from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig


@pytest.fixture
def base_tool(mock_config):
    class ConcreteBase(BaseAzureDevOpsWorkItemTool):
        name: str = "test_base"
        description: str = "test"
        def execute(self, **kwargs): ...
        def is_safe(self, args: dict) -> bool: return True
    return ConcreteBase(config=mock_config)


class TestTransformWorkItem:
    def test_valid_simple(self, base_tool):
        result = base_tool._transform_work_item('{"fields": {"System.Title": "Hello"}}')
        assert result == [{"op": "add", "path": "/fields/System.Title", "value": "Hello"}]

    def test_missing_fields_key(self, base_tool):
        with pytest.raises(ToolException, match="'fields' property is missing"):
            base_tool._transform_work_item('{"title": "oops"}')

    def test_malformed_json_actionable_message(self, base_tool):
        with pytest.raises(ToolException) as exc_info:
            base_tool._transform_work_item('{"fields": {"System.Title": "bad"')
        msg = str(exc_info.value)
        # Must NOT be double-wrapped
        assert msg.count("work_item_json") == 1
        # Must include position hint
        assert "char" in msg or "line" in msg or "column" in msg
        # Must include field guidance
        assert "escape" in msg.lower() or "quote" in msg.lower() or "html" in msg.lower()

    def test_valid_html_description(self, base_tool):
        payload = json.dumps({
            "fields": {
                "System.Title": "Story",
                "System.Description": "<p>Hello <b>world</b></p>",
            }
        })
        result = base_tool._transform_work_item(payload)
        desc = next(op for op in result if op["path"] == "/fields/System.Description")
        assert desc["value"] == "<p>Hello <b>world</b></p>"

    def test_valid_long_html_with_quotes(self, base_tool):
        long_html = '<p>' + ('A' * 3000) + ' said &quot;hello&quot;</p>'
        payload = json.dumps({
            "fields": {
                "System.Title": "Long Story",
                "System.Description": long_html,
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>AC1</li><li>AC2</li></ul>",
            }
        })
        result = base_tool._transform_work_item(payload)
        assert len(result) == 3

    def test_valid_tags_and_path_fields(self, base_tool):
        payload = json.dumps({
            "fields": {
                "System.Title": "Tagged",
                "System.Tags": "tag1; tag2; tag3",
                "System.AreaPath": "MyProject\\Team A",
                "System.IterationPath": "MyProject\\Sprint 1",
            }
        })
        result = base_tool._transform_work_item(payload)
        paths = [op["path"] for op in result]
        assert "/fields/System.Tags" in paths
        assert "/fields/System.AreaPath" in paths

    def test_valid_nested_json_string_in_value(self, base_tool):
        # A field value that is itself a JSON string (escaped) — valid JSON overall
        inner = json.dumps({"key": "value with \"quotes\""})
        payload = json.dumps({"fields": {"Custom.Metadata": inner}})
        result = base_tool._transform_work_item(payload)
        assert result[0]["value"] == inner
```

- [ ] **Step 2: Run tests to verify they fail as expected**

```
pytest tests/codemie_tools/azure_devops/work_item/test_tools.py::TestTransformWorkItem -v
```

Expected: `test_malformed_json_actionable_message` fails because the message is currently double-wrapped and lacks field guidance; other tests may pass or fail.

- [ ] **Step 3: Fix `_transform_work_item` at `tools.py:130-139`**

Replace the method body with an actionable single-wrap error. The new message includes the decode error position and explicit guidance for the LLM agent:

```python
def _transform_work_item(self, work_item_json: str) -> list[dict]:
    try:
        params = json.loads(work_item_json)
    except json.JSONDecodeError as e:
        raise ToolException(
            f"Failed to parse work_item_json: {e.msg} at line {e.lineno} column {e.colno} "
            f"(char {e.pos}). Ensure all double-quotes inside field values (e.g. HTML "
            f"attributes, description text) are escaped as \\\" and the JSON is well-formed."
        )

    if "fields" not in params:
        raise ToolException("The 'fields' property is missing from the work_item_json.")

    return [{"op": "add", "path": f"/fields/{field}", "value": value} for field, value in params["fields"].items()]
```

- [ ] **Step 4: Remove double-wrap in `CreateWorkItemTool.execute` at `tools.py:254-256`**

Change:
```python
        try:
            patch_document = self._transform_work_item(work_item_json)
        except Exception as e:
            raise ToolException(f"Issues during attempt to parse work_item_json: {str(e)}")
```

To (let `ToolException` propagate unmodified; only catch unexpected non-`ToolException` errors):
```python
        patch_document = self._transform_work_item(work_item_json)
```

- [ ] **Step 5: Remove double-wrap in `UpdateWorkItemTool.execute` at `tools.py:288-290`**

Apply the same change — remove the try/except wrapper around `_transform_work_item`:
```python
        patch_document = self._transform_work_item(work_item_json)
```

- [ ] **Step 6: Run the new tests to verify they pass**

```
pytest tests/codemie_tools/azure_devops/work_item/test_tools.py::TestTransformWorkItem -v
```

Expected: all 7 tests PASS.

---

### Task 2: Extend integration tests for `CreateWorkItemTool` and `UpdateWorkItemTool`

**Files:**
- Modify: `tests/codemie_tools/azure_devops/work_item/test_tools.py`

**Test-first: yes** — tests written before verifying they pass end-to-end through the tool's `execute` method.

- [ ] **Step 1: Add HTML-rich and edge-case tests to `TestCreateWorkItemTool`**

Append these test methods to the `TestCreateWorkItemTool` class (after the existing `test_create_work_item_success`):

```python
    def test_create_work_item_html_description(self, create_tool, mock_client):
        """Valid payload with HTML description and acceptance criteria must succeed."""
        payload = json.dumps({
            "fields": {
                "System.Title": "Story with HTML",
                "System.Description": "<p>As a user I want <b>feature</b></p>",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>AC1</li></ul>",
            }
        })
        mock_response = MagicMock()
        mock_response.id = 42
        mock_response.url = "http://test-url/42"
        mock_client.create_work_item.return_value = mock_response

        result = create_tool.execute(work_item_json=payload)

        assert "42" in result
        assert "created successfully" in result
        called_doc = mock_client.create_work_item.call_args.kwargs["document"]
        paths = [op["path"] for op in called_doc]
        assert "/fields/System.Description" in paths
        assert "/fields/Microsoft.VSTS.Common.AcceptanceCriteria" in paths

    def test_create_work_item_tags_and_paths(self, create_tool, mock_client):
        """Valid payload with tags and area/iteration path fields must succeed."""
        payload = json.dumps({
            "fields": {
                "System.Title": "Tagged Story",
                "System.Tags": "backend; api",
                "System.AreaPath": "Proj\\Team",
                "System.IterationPath": "Proj\\Sprint 2",
            }
        })
        mock_response = MagicMock()
        mock_response.id = 7
        mock_response.url = "http://test-url/7"
        mock_client.create_work_item.return_value = mock_response

        result = create_tool.execute(work_item_json=payload)

        assert "7" in result
        called_doc = mock_client.create_work_item.call_args.kwargs["document"]
        paths = [op["path"] for op in called_doc]
        assert "/fields/System.Tags" in paths

    def test_create_work_item_malformed_json_raises_tool_exception(self, create_tool, mock_client):
        """Malformed JSON must raise ToolException with actionable message, not double-wrapped."""
        from langchain_core.tools import ToolException
        with pytest.raises(ToolException) as exc_info:
            create_tool.execute(work_item_json='{"fields": {"System.Title": "bad"')
        msg = str(exc_info.value)
        assert msg.count("work_item_json") <= 1  # not double-wrapped
        assert "char" in msg or "line" in msg or "column" in msg

    def test_create_work_item_long_html_no_false_positive(self, create_tool, mock_client):
        """Long HTML payload must not trigger a false-positive parse error."""
        long_html = "<p>" + ("word " * 600) + "</p>"
        payload = json.dumps({
            "fields": {
                "System.Title": "Long",
                "System.Description": long_html,
            }
        })
        mock_response = MagicMock()
        mock_response.id = 99
        mock_response.url = "http://test-url/99"
        mock_client.create_work_item.return_value = mock_response

        result = create_tool.execute(work_item_json=payload)
        assert "99" in result
```

- [ ] **Step 2: Add HTML-rich and edge-case tests to `TestUpdateWorkItemTool`**

Append these test methods to the `TestUpdateWorkItemTool` class:

```python
    def test_update_work_item_html_description(self, update_tool, mock_client):
        """Valid payload with HTML description must succeed without parse error."""
        payload = json.dumps({
            "fields": {
                "System.Description": "<p>Updated <em>description</em></p>",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Done</li></ul>",
            }
        })
        mock_response = MagicMock()
        mock_response.id = 10
        mock_client.update_work_item.return_value = mock_response

        result = update_tool.execute(id=10, work_item_json=payload)

        assert "was updated" in result
        called_doc = mock_client.update_work_item.call_args.kwargs["document"]
        paths = [op["path"] for op in called_doc]
        assert "/fields/System.Description" in paths

    def test_update_work_item_malformed_json_raises_tool_exception(self, update_tool, mock_client):
        """Malformed JSON must raise ToolException with actionable message, not double-wrapped."""
        from langchain_core.tools import ToolException
        with pytest.raises(ToolException) as exc_info:
            update_tool.execute(id=5, work_item_json='{"fields": {"System.Title": unquoted}}')
        msg = str(exc_info.value)
        assert msg.count("work_item_json") <= 1
        assert "char" in msg or "line" in msg or "column" in msg

    def test_update_work_item_tags_and_path_fields(self, update_tool, mock_client):
        """Tags and path fields must parse without error."""
        payload = json.dumps({
            "fields": {
                "System.Tags": "qa; regression",
                "System.AreaPath": "Proj\\QA",
            }
        })
        mock_response = MagicMock()
        mock_response.id = 3
        mock_client.update_work_item.return_value = mock_response

        result = update_tool.execute(id=3, work_item_json=payload)
        assert "was updated" in result
```

- [ ] **Step 3: Add `import json` at the top of the test file if not already present**

Check line 1–20 of the test file; `import json` is not present in the existing imports. Add it after the existing stdlib imports (after `import base64`):

```python
import json
```

- [ ] **Step 4: Run the full test class**

```
pytest tests/codemie_tools/azure_devops/work_item/test_tools.py -v
```

Expected: all existing tests still PASS; all new tests PASS.

---

### Negative-constraint pass

The requirements state:
- **"Do NOT add json_repair or other lenient parsers"** — no task adds a new dependency or import beyond `json` stdlib. Task 1 Step 3 uses only `json.JSONDecodeError`. Confirmed: no task violates this.
- **"No new dependencies"** — both tasks modify only existing files; `pyproject.toml` is untouched. Confirmed: no task adds a dependency.
- **"The latent same bug in test_plan tools is out of scope"** — neither task touches `src/codemie_tools/azure_devops/test_plan/`. Confirmed: no task violates the out-of-scope boundary.
- **No whole-suite quality gate tasks** — the calling flow owns lint/build/test gates. No such task appears here.

negative-constraints: all three stated constraints honored; no violation found.
