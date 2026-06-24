# Frontend Handoff — AgentCore Invocation Fix & Config Expansion

**Branch:** `EPMCDME-12240-agentcore`
**Date:** 2026-06-16

---

## 1. ⚠️ Breaking Changes

### 1a. `invocation_json` renamed to `configuration_json` in AgentCore runtime data

| | Before | After |
|---|---|---|
| Field name | `invocation_json: string` (required) | `configuration_json: string \| null` (optional) |

**Where it breaks:**
- Any component or hook that reads `bedrockAgentcoreRuntimeData.invocation_json` from an assistant response.
- The POST body for importing an AgentCore runtime (`POST /vendors/aws/agentcore-runtimes/import` or equivalent) — the `ImportAgentcoreRuntime` body field was `invocation_json`, now must be `configuration_json`.

**Fix:** Rename the field everywhere. Note the new field is nullable — handle `null` gracefully (treat as unconfigured/default).

---

## 2. New Endpoints

### GET `/vendors/{origin}/agentcore-runtimes/{vendor_entity_id}/endpoints`

Lists the prepared runtime endpoints for an AgentCore runtime entry.

**Query parameters:**

| Param | Type | Required | Default |
|---|---|---|---|
| `setting_id` | `string` | yes | — |
| `page` | `number` | no | `0` |
| `per_page` | `number` | no | `12` |
| `next_token` | `string` | no | — |

**Response:**
```typescript
{
  data: AgentcoreEndpointEntity[];
  pagination: {
    next_token: string | null;  // URL-encoded cursor; null means no more pages
  };
}

interface AgentcoreEndpointEntity {
  id: string | null;
  name: string | null;
  status: EndpointStatus;          // see §4
  description: string | null;
  liveVersion: string | null;
  targetVersion: string | null;
  createdAt: string | null;        // ISO timestamp
  updatedAt: string | null;        // ISO timestamp
  aiRunId: string | null;          // internal, can be ignored
  configurationJson: string | null; // JSON string — see §4 for schema
}
```

**Status codes:** `200 OK`, `404` if `entity` path param is not `agentcore-runtimes`.

**Pagination style:** Cursor-based. Pass `next_token` from previous response as `next_token` query param for the next page. `null` means end of results.

**Notes:** Only works for `entity = agentcore-runtimes`. Other entity types return 404.

---

## 3. Modified Endpoints

### DELETE `/vendors/{origin}/{entity}/{entity_id}` — new 403 status code

This endpoint previously returned only `404` and `500`. It now also returns:

| Code | Meaning |
|---|---|
| `403 Forbidden` | User does not have permission to delete this entity |
| `404 Not Found` | Entity does not exist (unchanged) |
| `500 Internal Server Error` | Deletion failed (unchanged) |

**Affected components:** Any UI that calls the delete/unimport endpoint for vendor entities. Add a handler for the `403` case (e.g., show "You don't have permission to remove this" instead of a generic error).

### POST `/vendors/.../import` — `ImportAgentcoreRuntime` body changes

For AgentCore runtime imports, the request body now accepts two new optional fields:

```typescript
interface ImportAgentcoreRuntime {
  setting_id: string;
  id: string;
  agentcoreRuntimeEndpointName: string;
  configuration_json: string;       // ← was invocation_json (breaking rename)
  assistant_name?: string;          // NEW — optional display name for the created assistant
  assistant_description?: string;   // NEW — optional description for the created assistant
}
```

If your import form currently pre-fills assistant name/description some other way, consider passing them here to avoid a round-trip edit.

---

## 4. New/Changed Data Shapes

### `EndpointStatus` enum — all possible values

| Value | Recommended UI state |
|---|---|
| `"PREPARED"` | Show green badge / "Ready" |
| `"NOT_PREPARED"` | Show grey badge / "Not prepared" |
| `"VERSION_DRIFT"` | Show yellow warning badge / "Update available" |
| `"DELETED_ON_AWS"` | Show red/disabled badge / "Deleted on AWS" — treat as unimportable |

### `configuration_json` schema

This is a JSON **string** (not object) stored on `BedrockAgentcoreRuntimeData` and each `AgentcoreEndpointEntity`. When parsed, the structure is:

```typescript
interface AgentcoreConfiguration {
  request?: {
    message_path?: string;          // default "message" — dot-path for user query
    history?: {
      history_path: string;         // dot-path where turn array is placed
      role_path?: string;           // default "role"
      message_path?: string;        // default "content"
      user_role?: string;           // default "user"
      assistant_role?: string;      // default "assistant"
    } | null;
    extra_payload?: Record<string, unknown> | null;  // static fields merged into every request
  };
  response?: {
    streaming: boolean;
    body?: {                        // required when streaming = false
      text_path: string;            // dot-path to the answer text
      reasoning?: {
        thoughts_path?: string;     // NEW — dot-path to thoughts array (e.g. "thoughts")
        text_path: string;          // dot-path to text within each thought item
        name_path?: string;
        args_path?: string;
        active_path?: string;       // streaming only
      } | null;
    };
    chunk?: {                       // required when streaming = true
      text_path: string;
      reasoning?: { ... } | null;
    };
  };
}
```

**Complete filled example:**
```json
{
  "request": {
    "message_path": "inputs.question",
    "history": {
      "history_path": "inputs.chat_history",
      "user_role": "human",
      "assistant_role": "ai"
    }
  },
  "response": {
    "streaming": false,
    "body": {
      "text_path": "output",
      "reasoning": {
        "thoughts_path": "thoughts",
        "text_path": "text",
        "name_path": "tool",
        "args_path": "params"
      }
    }
  }
}
```

**UI note:** If your configuration editor renders `configuration_json` as a form, add the new `thoughts_path` field to the reasoning section alongside `text_path`. It is optional and defaults to `null` (legacy flat-path extraction).

---

## 5. Streaming / Real-time Changes

No new SSE or WebSocket frame types. No changes to existing streaming frame handling required.

---

## 6. What Requires No Frontend Changes

- **`thoughts_path` parser logic** — purely internal to how the backend extracts reasoning thoughts from AgentCore responses. No new fields in SSE frames or chat response payloads.
- **Chat history threading** — `chat_history` is now correctly sourced from `request.history` inside the Bedrock assistant agent. The API contract for sending history is unchanged.
- **AgentCore runtime service refactor** — `bedrock_agentcore_runtime_service.py` was significantly reorganised but the HTTP API surface is the same.
- **Exception class changes** — `EntityAccessDenied`, `EntityDeletionError`, `EntityNotFound` are backend-only; the frontend sees HTTP status codes only.
- **Test-only changes** — all files under `tests/` are invisible to the frontend.

---

## 7. Frontend Action Checklist

- [ ] Rename `invocation_json` → `configuration_json` in the `BedrockAgentcoreRuntimeData` TypeScript type and all components that read it
- [ ] Rename `invocation_json` → `configuration_json` in the `ImportAgentcoreRuntime` POST request body (import form)
- [ ] Handle `configuration_json: null` gracefully wherever the field was previously assumed to be a non-null string
- [ ] Add `assistant_name` and `assistant_description` optional fields to the AgentCore runtime import form (or POST body type)
- [ ] Add `GET /vendors/aws/agentcore-runtimes/{id}/endpoints` API call + endpoint list view/component
- [ ] Handle `EndpointStatus.DELETED_ON_AWS` in the endpoint status badge (currently unhandled if the component only knew PREPARED / NOT_PREPARED)
- [ ] Handle `EndpointStatus.VERSION_DRIFT` in the endpoint status badge
- [ ] Add `403 Forbidden` error handling to the delete/unimport vendor entity call
- [ ] Add `thoughts_path` input field to the AgentCore configuration JSON editor (optional, in the reasoning section)
