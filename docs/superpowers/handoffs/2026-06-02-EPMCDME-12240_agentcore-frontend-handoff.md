# Frontend Handoff — EPMCDME-12240: AgentCore configuration_json

**Branch:** `EPMCDME-12240_agentcore-asst`  
**Date:** 2026-06-02

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

Remove any client-side validation requiring `__QUERY_PLACEHOLDER__` — the backend no longer enforces it.

---

## ⚠️ Breaking Changes — None for other endpoints

---

## New/Changed Data Shapes

### `configuration_json` — two accepted formats

**Any valid JSON string is accepted.** Two patterns:

**Legacy — arbitrary JSON template:**
```json
{ "message": "hello" }
```
Sent as-is to the AgentCore runtime at invocation time.

**Structured format** — activated when the JSON has a top-level `"response"` key:

```typescript
type ConfigurationJson = {
  request?: {
    message_path?: string;   // optional, default "message"
                             // dot-notation: "input" → body.input, "input.query" → body.input.query
  };
  response: {                // required — triggers structured mode
    streaming: boolean;      // false = single JSON response, true = SSE stream

    body?: {                 // required when streaming=false
      text_path: string;     // dot-notation path to answer text, e.g. "output", "result.answer"
      reasoning?: ReasoningConfig;
    };

    chunk?: {                // required when streaming=true
      text_path: string;     // dot-notation path to text in each SSE chunk
      reasoning?: ReasoningConfig;
    };
  };
};

type ReasoningConfig = {
  text_path: string;     // required — path to thought content
  active_path?: string;  // optional — streaming only: boolean field indicating chunk in-progress
  name_path?: string;
  args_path?: string;
};
```

**Path notation:** `"result.answer"` → `data.result.answer`. `"choices.0.text"` → `data.choices[0].text`.

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
      "reasoning": { "text_path": "thinking", "active_path": "in_progress" }
    }
  }
}
```

### `configurationJson` on endpoint entities

`AgentcoreEndpointEntity.configurationJson` (and detail entity) now returns the stored value. Update any field read from the endpoint detail response: `invocationJson` → `configurationJson`.

### Import request — new optional assistant fields

`POST /v1/vendors/aws/agentcore-runtimes` accepts two new optional fields:

- `assistant_name?: string` — custom display name for the created assistant. Defaults to `"{runtimeId}:{endpointName}"` when omitted.
- `assistant_description?: string` — custom description. Defaults to the description from AWS endpoint metadata when omitted.

---

## Streaming / Real-time Changes

AgentCore assistants now emit `thought` frames in the SSE stream when `reasoning` is configured. Uses the existing `StreamedGenerationResult` frame shape — no changes needed if thought frames are already rendered for other assistant types.

---

## What Requires No Frontend Changes

- Existing imported assistants auto-migrate `invocation_json` → `configuration_json` on load; no re-import needed
- Path resolution and response parsing are server-side only
- ARN bug fix was internal

---

## Frontend Action Checklist

- [ ] Rename `invocation_json` → `configuration_json` in POST import payload
- [ ] Remove client-side `__QUERY_PLACEHOLDER__` validation if present
- [ ] Update endpoint entity field reads: `invocationJson` → `configurationJson`
- [ ] Add optional `assistant_name` and `assistant_description` fields to the import form
- [ ] If exposing a config editor: support structured format fields (`request.message_path`, `response.streaming`, `body.text_path` / `chunk.text_path`)
