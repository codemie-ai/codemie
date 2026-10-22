# A2A wrapper guide: put an A2A interface in front of your agent

This guide explains how to wrap an existing agent so that CodeMie (or any A2A 1.0 client) can call it, and how CodeMie itself wraps its assistants for external A2A clients. It complements the [A2A integration doc](a2a-integration.md), which covers setup, supported operations, limitations and failure behaviour.

Worked examples come from the demo agents in the separate repository [ai-run-demo-agents](https://github.com/epam-gen-ai-run/ai-run-demo-agents) (paths below are relative to that repo, under `python/agents/`):

| Agent | Source | Style |
|---|---|---|
| `text-insights-agent` | `text-insights-agent/agent.py`, `text-insights-agent/__main__.py` | non-streaming: answers with one `message` |
| `howto-guide-agent` | `howto-guide-agent/agent.py`, `howto-guide-agent/__main__.py` | streaming: answers with a `task` and a sequence of events |

Both use the official `a2a-sdk` (1.x). Code is deliberately not copied here: the agents repo is still evolving, so read the linked files for the current implementation. This guide was written against the repo state at 2026-10-01 and the executor structure described below was read from source, not re-run beyond the wire captures. The agent logic (`agent.py`) knows nothing about A2A; all protocol work sits in an *executor* class in `__main__.py`. That split is the pattern to copy.

## 1. The mapping in one picture

```
A2A request                                  your agent
-----------                                  ----------
message.parts[].text   -- join text parts -> invoke(text)         (agent input)
message.contextId      -- conversation id -> your session/thread id (optional)
message.taskId         -- continue a task -> your task/run id       (optional)

your result            -- a message       -> Message { role: ROLE_AGENT, parts }     (SendMessage, no task)
                       -- a task          -> Task { status, artifacts, history }     (SendMessage, task)
                       -- a stream        -> task, artifactUpdate*, statusUpdate(final state)  (SendStreamingMessage)
```

## 2. Request: A2A message to agent input

An A2A request carries a `message`:

```json
{"jsonrpc":"2.0","id":"1","method":"SendMessage",
 "params":{"message":{"messageId":"d1","contextId":"<conversation id>","role":"ROLE_USER",
                      "parts":[{"text":"Customer says: great product, slow shipping."}]}}}
```

- **Text parts to input.** Join the `text` of the message's parts and hand that string to the agent. In the SDK this is `context.get_user_input()`; both demo executors do `self.agent.invoke(context.get_user_input())`. CodeMie sends text parts only, and its own endpoint accepts text only (an empty `parts` list is rejected with `-32602`), so a wrapper has no need to handle other part types for CodeMie.
- **`contextId`.** CodeMie sets `contextId` to the chat conversation id, so all messages of one chat share it. If your agent keeps conversation memory, use `contextId` as the session or thread key. The demo agents are stateless and ignore it.
- **`taskId`.** Present when the client continues an existing task. A stateless wrapper can ignore it; a stateful one looks the task up and either continues it or answers that it is finished.
- **Headers.** Every request carries `A2A-Version: 1.0`. A wrapper must answer other versions with the JSON-RPC error `-32009` (the SDK does this for you). CodeMie also sends credentials (`Authorization` etc.) when an integration is configured; validating them is the wrapper's job.

## 3. Response: agent result to A2A

### 3.1 Non-streaming: answer with a Message (`text-insights-agent`)

When the answer is short and ready in one step, return a single `message`; no task is created.

The executor in `text-insights-agent/__main__.py` (`TextInsightsExecutor.execute`) reads the user text, calls the agent, formats the result as text and enqueues a single `message` event. See the source for the code.

Wire result (captured live):

```json
{"result":{"message":{"messageId":"e3701f54-...","role":"ROLE_AGENT","parts":[{"text":"Status: completed\nSentiment: ..."}]}},"id":"1","jsonrpc":"2.0"}
```

CodeMie accepts a `{"message": ...}` result and a `{"task": ...}` result for `SendMessage`.

The Agent Card for such an agent declares `capabilities.streaming: false`, and CodeMie then calls `SendMessage`.

### 3.2 Streaming: answer with a Task and events (`howto-guide-agent`)

For multi-part or long-running work, create a task, then emit events. The executor enqueues, in order: the task (`TASK_STATE_SUBMITTED`), one artifact event per result part, and a final status event carrying a terminal state.

The executor in `howto-guide-agent/__main__.py` (`HowToGuideExecutor.execute`) enqueues, in this order: (1) a task built from the user message, (2) one text artifact event per step, (3) a final status event with `TASK_STATE_COMPLETED`. It maps an agent-level error to `TASK_STATE_FAILED`. See the source for the code and the SDK helper names.

Wire frames (captured live; `SendStreamingMessage`, `Content-Type: text/event-stream`):

```
data: {"result": {"task": {"id": "ef1947b2-...", "contextId": "8cf1416d-...", "status": {"state": "TASK_STATE_SUBMITTED"}, ...}}}
data: {"result": {"artifactUpdate": {"taskId": "ef1947b2-...", "artifact": {"artifactId": "step-1", "parts": [{"text": "1. Place an egg in a saucepan ..."}]}}}}
...
data: {"result": {"statusUpdate": {"taskId": "ef1947b2-...", "status": {"state": "TASK_STATE_COMPLETED", ...}}}}
```

Rules that matter for a client such as CodeMie:

- **The stream must end in a terminal state** (`TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, `TASK_STATE_CANCELED`, ...). CodeMie stops reading when it sees the final status; a stream that just closes without one leaves the client without a verdict. There is no `final` flag in protocol 1.0, the terminal state is the end marker.
- **Artifacts are the result, status is the progress.** Put the answer text in `artifactUpdate` parts; use `statusUpdate` for state changes and short status messages. CodeMie shows artifact text as the chat answer, one chunk per artifact event.
- **Use stable `artifactId`s.** `append=False, last_chunk=True` per artifact means each step is a complete artifact. To stream one artifact in pieces, reuse the same `artifactId` with `append=True` and set `last_chunk=True` on the last piece.
- **Errors map to a failed task.** `howto-guide-agent` turns its agent-level `status == "error"` into `TASK_STATE_FAILED` with the error text, instead of raising.
- **Declare it on the card.** `capabilities.streaming: true`; CodeMie then calls `SendStreamingMessage`.

Note: in the demo agent the steps are produced in one LLM call and then emitted as events one by one, so the events are incremental on the wire but not produced incrementally. If your agent can yield partial output while it works, enqueue an `artifactUpdate` each time you have a piece.

### 3.3 Message or Task: which one?

| Return | When |
|---|---|
| `Message` | one short answer, no need to track progress or fetch it later |
| `Task` (+ artifacts) | work takes time, has several outputs, or the client may poll `GetTask` or cancel |

## 4. The Agent Card

The card is how CodeMie discovers the agent. `__main__.py` builds it and serves it at `/.well-known/agent-card.json`:

`__main__.py` of each demo agent builds the card with the SDK (`AgentCard`, `AgentInterface`, `AgentCapabilities`, `AgentSkill`); the fields that matter to CodeMie are `supportedInterfaces[].url` with binding `JSONRPC`, `capabilities.streaming`, the input/output modes and `skills`.

`supported_interfaces[].url` is where CodeMie sends requests, so it must be reachable **from the CodeMie process**: build it from a host name the CodeMie container resolves (the demo agents take `--host`), never `0.0.0.0`. See the Docker note in the integration doc.

## 5. Cancellation

CodeMie can send `CancelTask`. An agent that cannot cancel raises the SDK's unsupported-operation error (both demo executors do this); the client receives a protocol error instead of a hang.

## 6. The other direction: how CodeMie wraps its own assistants

CodeMie applies the same mapping server-side, in `src/codemie/rest_api/a2a/server/codemie_task_manager.py`:

- **Request in.** The message's text parts become the chat query (`text=query`) and `contextId` becomes the conversation id (`conversation_id`), so repeated calls with the same `contextId` share one conversation. A new `contextId` is generated when the client sends none.
- **Result out.** `SendMessage` returns a `task` whose `artifacts` carry the assistant's answer and whose `history` echoes the user message; `SendStreamingMessage` emits `task` (SUBMITTED), `statusUpdate` (WORKING), `artifactUpdate`, then `statusUpdate` (COMPLETED), exactly the sequence in section 3.2.
- **Unsupported options** answer protocol errors rather than being ignored: `returnImmediately` gives `-32004`; output modes without `text/plain` give `-32005`.

Captured responses for this direction are in section 3 of the integration doc.

## 7. Checklist for your own wrapper

1. Keep agent logic free of A2A types; build an executor that turns `context.get_user_input()` into a call and the result into events.
2. Publish an Agent Card at `/.well-known/agent-card.json` with a reachable JSON-RPC `supportedInterfaces[].url`.
3. Enforce `A2A-Version: 1.0` (the SDK does).
4. Streaming: submitted task first, artifacts next, one terminal status last.
5. Return a failed task or a protocol error, never a silent empty answer.
6. Test with curl exactly as in the integration doc, then register the agent in CodeMie and chat.
