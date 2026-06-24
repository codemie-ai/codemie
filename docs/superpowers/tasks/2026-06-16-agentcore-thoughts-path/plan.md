# AgentCore Configurable Thoughts Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `thoughts_path: Optional[str] = None` to `AgentcoreReasoningConfig` so that non-streaming AgentCore endpoints returning structured thought arrays (e.g. `{"thoughts": [{"text": "..."}]}`) can configure the array location and per-item text path independently.

**Architecture:** Single new field on the config model; a new `_extract_thoughts_from_array` helper on the parser invoked when `thoughts_path` is set; existing scalar/flat-list `_extract_thoughts` path unchanged for backward compatibility.

**Tech Stack:** Python, Pydantic v2, pytest

---

## File Map

| File | Change |
|---|---|
| `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py` | Add `thoughts_path: Optional[str] = None` to `AgentcoreReasoningConfig` (line 26) |
| `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py` | Branch `_extract_thoughts` on `thoughts_path`; add `_extract_thoughts_from_array` after `_extract_thoughts` |
| `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py` | Append 7 new tests at end of file |

---

### Task 1: Write the failing tests

**Test-first: yes — `test_parse_json_thoughts_path_extracts_array_of_objects` (and 6 companions) all fail with `ValidationError` because `thoughts_path` does not yet exist on `AgentcoreReasoningConfig`**

**Files:**
- Modify: `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py`

- [ ] **Step 1: Append the 7 new tests at the end of the test file**

```python
# --- thoughts_path array extraction ---


def test_parse_json_thoughts_path_extracts_array_of_objects():
    parser = AgentcoreResponseParser()
    body = json.dumps({
        "output": "The answer",
        "thoughts": [{"text": "I thought X"}, {"text": "I considered Y"}],
    }).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    text, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert text == "The answer"
    assert len(thoughts) == 2
    assert thoughts[0].message == "I thought X"
    assert thoughts[1].message == "I considered Y"


def test_parse_json_thoughts_path_with_name_and_args():
    parser = AgentcoreResponseParser()
    body = json.dumps({
        "output": "done",
        "steps": [
            {"text": "reasoning A", "tool": "SearchTool", "params": {"q": "test"}},
            {"text": "reasoning B", "tool": "AnalysisTool", "params": {"x": 1}},
        ],
    }).encode()
    reasoning = AgentcoreReasoningConfig(
        thoughts_path="steps",
        text_path="text",
        name_path="tool",
        args_path="params",
    )
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 2
    assert thoughts[0].message == "reasoning A"
    assert thoughts[0].author_name == "SearchTool"
    assert '"q": "test"' in thoughts[0].input_text
    assert thoughts[1].author_name == "AnalysisTool"


def test_parse_json_thoughts_path_missing_array():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer"}).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts == []


def test_parse_json_thoughts_path_non_list():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thoughts": "not a list"}).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts == []


def test_parse_json_thoughts_path_empty_array():
    parser = AgentcoreResponseParser()
    body = json.dumps({"output": "answer", "thoughts": []}).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert thoughts == []


def test_parse_json_thoughts_path_skips_null_text():
    parser = AgentcoreResponseParser()
    body = json.dumps({
        "output": "answer",
        "thoughts": [
            {"text": "real thought"},
            {"other": "no text here"},
            {"text": "another thought"},
        ],
    }).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 2
    assert thoughts[0].message == "real thought"
    assert thoughts[1].message == "another thought"


def test_parse_json_thoughts_path_scalar_items():
    parser = AgentcoreResponseParser()
    body = json.dumps({
        "output": "answer",
        "thoughts": ["plain string 1", "plain string 2"],
    }).encode()
    reasoning = AgentcoreReasoningConfig(thoughts_path="thoughts", text_path="text")
    _, thoughts = parser.parse_json(body, _json_response_config("output", reasoning))
    assert len(thoughts) == 2
    assert thoughts[0].message == "plain string 1"
    assert thoughts[1].message == "plain string 2"
```

- [ ] **Step 2: Run the first test to confirm it fails**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py::test_parse_json_thoughts_path_extracts_array_of_objects -v
```

Expected: **FAILED** — `pydantic_core._pydantic_core.ValidationError` because `thoughts_path` is not a field on `AgentcoreReasoningConfig`.

---

### Task 2: Implement the feature and commit

**Test-first: yes — tests from Task 1 drive this**

**Files:**
- Modify: `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py:23-29`
- Modify: `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py:205-224`

- [ ] **Step 1: Add `thoughts_path` to `AgentcoreReasoningConfig`**

In `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py`, replace the `AgentcoreReasoningConfig` class body:

```python
class AgentcoreReasoningConfig(BaseModel):
    """Paths used to extract thought content from each response body or SSE chunk."""

    thoughts_path: Optional[str] = None  # dot-notation path to the thoughts array in the response body
    text_path: str
    name_path: Optional[str] = None
    args_path: Optional[str] = None
    active_path: Optional[str] = None  # streaming only — boolean field per chunk indicating thought in-progress
```

- [ ] **Step 2: Replace `_extract_thoughts` and add `_extract_thoughts_from_array` in the parser**

In `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py`, replace the `_extract_thoughts` method (lines 205–224) with:

```python
    def _extract_thoughts(self, data: dict, reasoning: AgentcoreReasoningConfig) -> list[Thought]:
        """Extract one or more Thought objects from a JSON response using reasoning path config."""
        if reasoning.thoughts_path is not None:
            return self._extract_thoughts_from_array(data, reasoning)

        text_val = resolve_json_path(data, reasoning.text_path)
        if text_val is None:
            logger.debug(f"[AgentCore] Reasoning path {reasoning.text_path!r} resolved to None")
            return []

        if not isinstance(text_val, list):
            name = resolve_json_path(data, reasoning.name_path) if reasoning.name_path else None
            args = resolve_json_path(data, reasoning.args_path) if reasoning.args_path else None
            return [self._make_thought(str(text_val), name, args)]

        name_vals = resolve_json_path(data, reasoning.name_path) if reasoning.name_path else None
        args_vals = resolve_json_path(data, reasoning.args_path) if reasoning.args_path else None
        logger.debug(f"[AgentCore] Reasoning fan-out: {len(text_val)} thoughts extracted")
        return [
            self._make_thought(str(text), self._pick(name_vals, i), self._pick(args_vals, i))
            for i, text in enumerate(text_val)
            if text is not None
        ]

    def _extract_thoughts_from_array(self, data: dict, reasoning: AgentcoreReasoningConfig) -> list[Thought]:
        thoughts_array = resolve_json_path(data, reasoning.thoughts_path)
        if not isinstance(thoughts_array, list):
            logger.debug(
                f"[AgentCore] thoughts_path {reasoning.thoughts_path!r} resolved to "
                f"{type(thoughts_array).__name__}, expected list"
            )
            return []

        result = []
        for item in thoughts_array:
            if isinstance(item, dict):
                text = resolve_json_path(item, reasoning.text_path)
                name = resolve_json_path(item, reasoning.name_path) if reasoning.name_path else None
                args = resolve_json_path(item, reasoning.args_path) if reasoning.args_path else None
            else:
                text = item
                name = None
                args = None
            if text is None:
                continue
            result.append(self._make_thought(str(text), name, args))

        logger.debug(f"[AgentCore] thoughts_path fan-out: {len(result)} thoughts extracted")
        return result
```

- [ ] **Step 3: Run the 7 new tests to confirm they pass**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py -k "thoughts_path" -v
```

Expected: all 7 tests **PASSED**.

- [ ] **Step 4: Run the full test module to confirm no regressions**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py -v
```

Expected: all tests **PASSED**.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/agentcore/agentcore_config.py \
        src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py \
        tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py
git commit -m "EPMCDME-12240: Add thoughts_path to AgentcoreReasoningConfig for array-of-objects extraction"
```
