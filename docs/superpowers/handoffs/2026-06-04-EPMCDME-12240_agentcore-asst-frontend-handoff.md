# Frontend Handoff — EPMCDME-12240: AgentCore history passing

**Branch:** `EPMCDME-12240_agentcore-asst`
**Date:** 2026-06-04
**Supersedes / supplements:** `2026-06-02-EPMCDME-12240_agentcore-frontend-handoff.md`

---

## 1. ⚠️ Breaking Changes

**None.** All changes in this increment are additive and backward-compatible.

---

## 2. New Endpoints

None.

---

## 3. Modified Endpoints

None — no route signatures changed.

---

## 4. New/Changed Data Shapes

### `configuration_json` — direction of each config section

`configuration_json` has two distinct concerns with opposite directions. This distinction matters for the UI:

| Section | Direction | Purpose |
|---|---|---|
| `request` (incl. `history`) | **Outbound — what we *send* to the runtime** | Tells the backend how to shape the JSON it posts to the AgentCore endpoint: where to place the query, whether to attach prior conversation turns, what format each turn uses. |
| `response` (incl. `body`, `chunk`, `reasoning`) | **Inbound — what we *extract* from the runtime** | Tells the backend where to find the answer text and optional thought content in the runtime's JSON response or SSE stream. The frontend never sees the raw runtime response; the backend extracts and forwards only the resolved text and thoughts. |

In a config editor, surface these as two labelled groups: **"Request — what to send"** and **"Response — what to extract"**.

---

### `configuration_json` full type (updated)

```typescript
type ConfigurationJson = {
  // ─── OUTBOUND: shapes the payload sent to the AgentCore runtime ───────────
  request?: {
    message_path?: string;   // dot-notation path for the user query (default: "message")
    history?: HistoryConfig; // NEW — omit to never send history; present = inject prior turns
  };

  // ─── INBOUND: tells the backend what to extract from the runtime response ─
  response: {
    streaming: boolean;      // false = single JSON blob; true = SSE stream

    // Required when streaming=false — extract the answer from a JSON response body
    body?: {
      text_path: string;     // dot-notation path to the answer text
      reasoning?: ReasoningConfig; // optional — extract thought content from the same body
    };

    // Required when streaming=true — extract the answer from each SSE chunk
    chunk?: {
      text_path: string;     // dot-notation path to the text delta in each chunk
      reasoning?: ReasoningConfig; // optional — extract thought content from each chunk
    };
  };
};

// ─── Outbound: per-turn shape when injecting history into the request ────────
type HistoryConfig = {
  history_path: string;      // REQUIRED — dot-notation path for the turns array, e.g. "messages"
  role_path?: string;        // field name for the role in each turn object (default: "role")
  message_path?: string;     // field name for the text in each turn object (default: "content")
  user_role?: string;        // role label emitted for user turns (default: "user")
  assistant_role?: string;   // role label emitted for assistant turns (default: "assistant")
};

// ─── Inbound: paths used to extract thought content from the runtime response ─
type ReasoningConfig = {
  text_path: string;         // REQUIRED — dot-notation path to the thought text
  active_path?: string;      // STREAMING ONLY — boolean field per chunk indicating thought in-progress
  name_path?: string;        // optional — dot-notation path to the thought/tool name
  args_path?: string;        // optional — dot-notation path to tool arguments
};
```

**Key rules:**
- `history_path` is required when the `history` object is present; omitting it returns HTTP 422.
- `active_path` is only meaningful when `streaming: true`; it has no effect in non-streaming configs and should not be shown in a non-streaming editor.
- If `history` is absent, no history key is ever written to the outgoing payload — even if the conversation has prior messages.
- If `history` is present but the conversation has no prior messages, the history key is also omitted.

---

**Complete example — non-streaming, with history injection and thought extraction:**
```json
{
  "request": {
    "message_path": "query",
    "history": {
      "history_path": "messages",
      "user_role": "user",
      "assistant_role": "assistant"
    }
  },
  "response": {
    "streaming": false,
    "body": {
      "text_path": "output",
      "reasoning": { "text_path": "thinking" }
    }
  }
}
```

What gets **sent** to the AgentCore runtime:
```json
{
  "query": "What is the weather today?",
  "messages": [
    { "role": "user",      "content": "Hello" },
    { "role": "assistant", "content": "Hi there! How can I help?" }
  ]
}
```

What gets **extracted** from the runtime's JSON response:
- Answer text ← `response_body.output`
- Thought text ← `response_body.thinking` (forwarded as a thought frame to the frontend)

---

## 5. Streaming / Real-time Changes

None beyond what was documented in the 2026-06-02 handoff.

---

## 6. What Requires No Frontend Changes

- The history injection is entirely server-side. The frontend sends user messages and receives the response as before — no new request/response fields.
- All internal threading changes (`invoke_agentcore_runtime`, `_build_agentcore_request`, `BedrockOrchestratorService`) are transparent to the API surface.
- Existing `configuration_json` values without a `history` block continue to work unchanged.

---

## 7. Frontend Action Checklist

- [ ] In the config editor, label the `request` section **"Request — what to send"** and the `response` section **"Response — what to extract"** so operators understand the direction of each field
- [ ] Add an optional **History** sub-section inside "Request — what to send" with fields: `history_path` (required when section enabled), `role_path`, `message_path`, `user_role`, `assistant_role`
- [ ] Hide (or disable) `active_path` in the reasoning config when `streaming: false` is selected; show it only for streaming configs
