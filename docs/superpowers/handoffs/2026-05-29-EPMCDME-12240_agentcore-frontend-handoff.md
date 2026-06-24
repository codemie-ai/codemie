# Frontend Handoff — EPMCDME-12240: AgentCore configuration_json

**Branch:** `EPMCDME-12240_agentcore`  
**Date:** 2026-05-29

---

## ⚠️ Breaking Changes

### Import request body field renamed

**Affected call:** `POST /v1/vendors/aws/agentcore-runtimes`

| | Before | After |
|---|---|---|
| Field name | `invocation_json` | `configuration_json` |
| Validation | must contain `__QUERY_PLACEHOLDER__` | any valid JSON string |

```typescript
// Before
{ setting_id, id, agentcoreRuntimeEndpointName, invocation_json: "..." }

// After
{ setting_id, id, agentcoreRuntimeEndpointName, configuration_json: "..." }
```

Update any form state, API client, or hook that constructs the import payload. The `__QUERY_PLACEHOLDER__` requirement is gone — any valid JSON is accepted.

---

## New/Changed Data Shapes

### `configuration_json` field

Accepts any valid JSON string. Two patterns are supported:

**Legacy — plain message template (still works):**
```json
{ "message": "hello" }
```
Any arbitrary JSON. At invocation time the backend sends it as-is to the AgentCore runtime.

**New structured format** — activated when the JSON has a top-level `"response"` key:

```typescript
type ConfigurationJson = {
  request?: {
    message_path?: string;   // required | optional | default: "message"
                             // dot-notation path where the user query is injected
                             // "input" → body.input
                             // "input.query" → body.input.query
  };
  response: {                // required — presence activates structured mode
    streaming: boolean;      // required | true = SSE stream, false = single JSON response

    body?: {                 // required when streaming=false
      text_path: string;     // required | dot-notation path to the answer text
                             // e.g. "output", "result.answer", "choices.0.text"
      reasoning?: ReasoningConfig;  // optional | enables thought frame emission
    };

    chunk?: {                // required when streaming=true
      text_path: string;     // required | path to extract text from each SSE chunk
      reasoning?: ReasoningConfig;  // optional
    };
  };
};

type ReasoningConfig = {
  text_path: string;    // required | path to thought/reasoning content
  active_path: string;  // required | boolean path — true=in progress, false=closed
  name_path?: string;   // optional | path to thought author name
  args_path?: string;   // optional | path to tool arguments
};
```

**Path notation:** dot-separated keys. `"result.answer"` → `data.result.answer`. `"choices.0.text"` → `data.choices[0].text`.

**Example — non-streaming:**
```json
{
  "request": { "message_path": "input" },
  "response": {
    "streaming": false,
    "body": { "text_path": "output" }
  }
}
```

**Example — streaming with thought extraction:**
```json
{
  "request": { "message_path": "input.query" },
  "response": {
    "streaming": true,
    "chunk": {
      "text_path": "delta",
      "reasoning": {
        "text_path": "thinking",
        "active_path": "in_progress"
      }
    }
  }
}
```

### `configurationJson` on endpoint entities

`AgentcoreEndpointEntity.configurationJson` (and `AgentcoreEndpointDetailEntity`) now returns the stored `configuration_json` value. If the UI reads this to pre-fill an import/edit form, update the field name from `invocationJson` → `configurationJson`.

---

## Streaming / Real-time Changes

AgentCore assistants now emit `thought` frames in the SSE stream when `reasoning` is configured in `configuration_json`. Uses the existing `StreamedGenerationResult` frame shape — no changes needed if thought frames are already rendered for other assistant types.

---

## What Requires No Frontend Changes

- `__QUERY_PLACEHOLDER__` legacy format still works at invocation time (used as a plain JSON template)
- Existing imported assistants are migrated automatically on load — no re-import needed
- Path resolution and response parsing are entirely server-side
- ARN bug fix was internal only

---

## Frontend Action Checklist

- [ ] Rename `invocation_json` → `configuration_json` in POST import payload
- [ ] Remove any client-side validation requiring `__QUERY_PLACEHOLDER__`
- [ ] Update any field read from endpoint entity: `invocationJson` → `configurationJson`
- [ ] If exposing a config editor: support the new structured format (show `request.message_path`, `response.streaming`, `body.text_path` / `chunk.text_path` fields)
