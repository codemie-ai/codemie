# AgentCore Configurable Thoughts Path

## Problem

`AgentcoreReasoningConfig.text_path` currently conflates two different roles depending on the response shape:

- When the AgentCore endpoint returns a **scalar** or **flat list** at the path, `_extract_thoughts` uses `text_path` directly against the full response body.
- When the endpoint returns an **array of objects** (e.g. `{"thoughts": [{"text": "..."}, {"text": "..."}]}`), there is no way to express "the array is at `thoughts` and the text within each item is at `text`". Configuring `text_path = "thoughts"` returns the raw dicts, which are stringified incorrectly.

The result: non-streaming AgentCore endpoints that return structured thought arrays cannot extract those thoughts correctly. There is no configurable path for the array location.

## Fix

Add `thoughts_path: Optional[str] = None` to `AgentcoreReasoningConfig`. When set, it is the dot-notation path to the thoughts **array** in the full response body; `text_path`, `name_path`, and `args_path` then apply **per item** (each resolved against the individual thought object, not the full response). When `thoughts_path` is `None` (the default), the existing scalar/flat-list behavior is preserved exactly.

## Scope

- Non-streaming only. `_handle_reasoning_chunk` (the SSE per-chunk path) is not changed — it does not receive a full array, so `thoughts_path` does not apply there.
- `active_path` (streaming-only field) is unchanged.
- All existing `AgentcoreReasoningConfig` values without `thoughts_path` continue to work without modification.

## Config Model

**File:** `src/codemie/service/aws_bedrock/agentcore/agentcore_config.py`

```python
class AgentcoreReasoningConfig(BaseModel):
    thoughts_path: Optional[str] = None  # NEW: dot-notation path to the thoughts array in the response
    text_path: str                        # per-item when thoughts_path set; full-response scalar/list otherwise
    name_path: Optional[str] = None
    args_path: Optional[str] = None
    active_path: Optional[str] = None    # streaming only
```

## Parser Change

**File:** `src/codemie/service/aws_bedrock/agentcore/agentcore_response_parser.py`

`_extract_thoughts` gains a new branch executed when `reasoning.thoughts_path` is set:

1. `resolve_json_path(data, reasoning.thoughts_path)` → must be a list; if `None` or not a list, return `[]` and log a debug message.
2. For each item in the list:
   - If the item is a `dict`: resolve `text_path`, `name_path`, `args_path` within the item using `resolve_json_path(item, path)`.
   - If the item is a scalar: use it directly as the text value; `name` and `args` are `None`.
3. Skip items where the resolved text is `None`.
4. Return the list of `Thought` objects.

When `reasoning.thoughts_path` is `None`: existing logic runs unchanged.

## Example Config JSON

```json
{
  "response": {
    "streaming": false,
    "body": {
      "text_path": "output",
      "reasoning": {
        "thoughts_path": "thoughts",
        "text_path": "text"
      }
    }
  }
}
```

For the response `{"output": "The answer", "thoughts": [{"text": "I thought X"}, {"text": "I considered Y"}]}`, this produces two `Thought` objects with messages `"I thought X"` and `"I considered Y"`.

## Tests

**File:** `tests/codemie/service/aws_bedrock/agentcore/test_agentcore_response_parser.py`

| Test | Scenario |
|---|---|
| `test_parse_json_thoughts_path_extracts_array_of_objects` | Happy path: array of `{"text": "..."}` objects; thoughts extracted correctly |
| `test_parse_json_thoughts_path_with_name_and_args` | Array items have `text`, `name`, `args`; all three are extracted per item |
| `test_parse_json_thoughts_path_missing_array` | `thoughts_path` set but key not present in response → `[]` |
| `test_parse_json_thoughts_path_non_list` | `thoughts_path` resolves to a scalar → `[]` |
| `test_parse_json_thoughts_path_empty_array` | `thoughts_path` resolves to `[]` → `[]` |
| `test_parse_json_thoughts_path_skips_null_text` | Array contains item where `text_path` resolves to `None` → item skipped |
| `test_parse_json_thoughts_path_scalar_items` | Array contains plain strings (not dicts) → each string becomes a thought |

## Out of Scope

- Streaming per-chunk path (`_handle_reasoning_chunk`).
- Forwarding non-streaming thoughts to the UI in `generate()` — separate concern.
- Changes to `AgentcoreOutputConfig`, `AgentcoreResponseConfig`, or the request builder.
