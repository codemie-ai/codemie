# Frontend Handoff — EPMCDME-12240: AgentCore extra_payload

**Branch:** `EPMCDME-12240_agentcore-asst`
**Date:** 2026-06-05
**Supersedes / supplements:** `2026-06-04-EPMCDME-12240_agentcore-asst-frontend-handoff.md`

---

## 1. ⚠️ Breaking Changes

None.

---

## 2. New Endpoints

None.

---

## 3. Modified Endpoints

None.

---

## 4. New/Changed Data Shapes

### `configuration_json.request.extra_payload` — new optional field

The `request` section of `configuration_json` now accepts an optional `extra_payload` object. Its fields are merged verbatim into every request payload sent to the AgentCore runtime endpoint, before the `message_path` and `history` fields are written (so those always win on key collision).

**Updated TypeScript type for the `request` section:**

```typescript
type RequestConfig = {
  message_path?: string;                       // default: "message"
  history?: HistoryConfig;                     // omit to never send history
  extra_payload?: Record<string, unknown>;     // NEW — static fields merged into every request
};
```

Rules:
- Must be a JSON object (not an array or scalar). HTTP 422 is returned if the value is invalid.
- `null` / absent → no extra fields added (existing behaviour unchanged).
- `message_path` and `history` writes always override any same-key value in `extra_payload`.

**Example:**
```json
{
  "request": {
    "message_path": "query",
    "extra_payload": {
      "sessionId": "abc123",
      "mode": "fast"
    }
  },
  "response": {
    "streaming": false,
    "body": { "text_path": "output" }
  }
}
```

---

## 5. Streaming / Real-time Changes

None.

---

## 6. What Requires No Frontend Changes

All existing `configuration_json` values without `extra_payload` continue to work unchanged.

---

## 7. Frontend Action Checklist

- [ ] Add optional **Extra Payload** field in the `configuration_json` editor under "Request — what to send" — accepts a JSON object; show a validation error if the user enters an array or non-object value
