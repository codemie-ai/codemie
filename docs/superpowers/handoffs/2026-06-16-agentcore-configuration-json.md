# New field: `thoughts_path` in AgentCore reasoning config

**Branch:** `EPMCDME-12240-agentcore`

## What changed

`AgentcoreReasoningConfig` (the `reasoning` object inside `response.body` of `configuration_json`) gains a new optional field:

| Field | Type | Default |
|---|---|---|
| `thoughts_path` | `string \| null` | `null` |

## What it does

When `thoughts_path` is set, it is the dot-notation path to the **array of thought objects** in the response body. The existing `text_path`, `name_path`, and `args_path` then act as **per-item** paths resolved against each element of that array.

When `thoughts_path` is `null` (default), behaviour is unchanged — `text_path` is resolved against the full response body as before.

## When to use it

Use `thoughts_path` when the AgentCore endpoint returns thoughts as an array of objects, e.g.:

```json
{
  "output": "Paris.",
  "thoughts": [
    { "text": "Searching for capitals.", "tool": "KBSearch", "params": { "q": "France" } },
    { "text": "Found the answer." }
  ]
}
```

Config:

```json
"reasoning": {
  "thoughts_path": "thoughts",
  "text_path": "text",
  "name_path": "tool",
  "args_path": "params"
}
```

Without `thoughts_path`, the same config would try to resolve `text_path` at the top level of the response body, which would return `null` for the structure above.

## Notes

- Items where `text_path` resolves to `null` are skipped silently.
- Plain string items (non-objects) in the array are used as the thought text directly.
- `thoughts_path` only applies to non-streaming (`response.body`). Streaming uses `active_path` per chunk and is unaffected.
