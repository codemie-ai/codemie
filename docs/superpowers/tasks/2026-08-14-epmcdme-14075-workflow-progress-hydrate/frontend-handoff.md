# Frontend handoff — EPMCDME-14075 (backend)

**Repo:** `codemie`  
**Branch:** `EPMCDME-14075_workflow-progress-hydrate`  
**For:** sibling `codemie-ui` run (workflow chat progress after page reload)

Backend hydrate + disconnect are shipped. The UI must consume the GET conversation payload below. Do not wait for SSE rejoin; poll this GET.

---

## What the backend now does

- `GET /conversations/{id}` rematerializes workflow stubs from persisted `WorkflowExecutionState` rows, including the **current IN PROGRESS** step.
- The assistant turn carries **execution identity and overall status**, so the UI can tell a run is still alive **between** steps (when no thought has `in_progress: true`).
- Closing the original SSE client (tab reload) does **not** stop the workflow. Later steps keep running; polling GET is enough.

Intra-step character-by-character token streaming after reload is **not** provided.

---

## Endpoint

Poll:

```
GET /conversations/{conversation_id}
```

(Existing conversation GET / history slice already materializes workflow refs. No new route.)

Optional Details APIs (already used by Workflow Details; not required if chat GET is consumed):

- `GET /v1/workflows/{workflow_id}/executions/{execution_id}`
- `GET /v1/workflows/{workflow_id}/executions/{execution_id}/states`

---

## JSON field names (match these exactly)

### Assistant message (`GeneratedMessage` / `ConfiguredModel` → **camelCase**)

| JSON field | Type | Meaning |
|---|---|---|
| `workflowExecutionRef` | `boolean` | `true` for a workflow-execution stub turn |
| `executionId` | `string` | Workflow execution id |
| `executionStatus` | `string` \| `null` | Overall run status (see values below) |
| `message` | `string` | Final/turn output. **Empty while the run is in progress** |
| `thoughts` | `Thought[]` | One card per visible execution state for this turn |

`executionStatus` values (enum `.value` strings):

- `"In Progress"`
- `"Succeeded"`
- `"Aborted"`
- `"Failed"`
- `"Interrupted"`
- `"Not Started"`
- `"AUTHENTICATION_REQUIRED"`

### Nested thoughts (`Thought` is a plain Pydantic model → **snake_case**)

Do **not** assume camelCase on thoughts (`inProgress` / `authorName` will not be present).

| JSON field | Type | Meaning |
|---|---|---|
| `id` | `string` | Stable state id (same across polls) |
| `author_name` | `string` | Step name (e.g. `"Find Items Todo"`) |
| `author_type` | `string` | `"WorkflowState"` |
| `message` | `string` | Step output; `""` while that step is in progress |
| `input_text` | `string` \| `null` | Step task / input (live cards’ Input) |
| `in_progress` | `boolean` | `true` iff this state’s status is `"In Progress"` |
| `interrupted` | `boolean` | `true` iff status is `"Interrupted"` |
| `aborted` | `boolean` | `true` iff status is `"Aborted"` |
| `children` | `Thought[]` | Empty for these state cards (token trees are not persisted) |

---

## Mid-run example (reload during a step)

```json
{
  "role": "Assistant",
  "workflowExecutionRef": true,
  "executionId": "<uuid>",
  "executionStatus": "In Progress",
  "message": "",
  "thoughts": [
    {
      "id": "<state-id>",
      "author_name": "Find Items Todo",
      "author_type": "WorkflowState",
      "message": "",
      "input_text": "Find changes to do in the document.",
      "in_progress": true,
      "interrupted": false,
      "aborted": false,
      "children": []
    }
  ]
}
```

## How the UI should render after reload

1. If `workflowExecutionRef` and `executionId` are set, treat the turn as a live/restored workflow execution (not a blank assistant answer).
2. Show each thought with `author_type === "WorkflowState"` as a step card:
   - `in_progress === true` → current step (same kind of progress UI as before refresh).
   - otherwise show `message` as the step result.
3. Keep polling GET while `executionStatus === "In Progress"`.
4. **Between steps:** there may be **no** `in_progress: true` thought. Use `executionStatus === "In Progress"` (and empty `message`) to keep showing “run still alive”. Do not treat that as a finished blank turn.
5. Stop treating the run as live when `executionStatus` is terminal (`Succeeded`, `Aborted`, `Failed`, `Interrupted`, …). Then `message` holds the final output.

## Out of scope for this backend (UI must not expect)

- SSE stream resume / `Last-Event-Id`
- Nested in-progress token chunks (`ThoughtConsumer` still skips those)
- camelCase thought fields
- Changes in `codemie-ui` (this file is the contract for that sibling run)
