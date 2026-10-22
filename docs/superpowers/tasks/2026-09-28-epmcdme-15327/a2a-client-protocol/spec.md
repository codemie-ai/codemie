# Spec: A2A client protocol v1.0 (EPMCDME-15327, slice 3)

## Goal

Upgrade the CodeMie A2A client (`src/codemie/rest_api/a2a/client/`: adapter, client, card_resolver,
remote_agent_connection) and its consumer `A2AAssistantHandler`
(`rest_api/handlers/assistant_handlers.py` ~1321-1478) to A2A v1.0 per
`.ai-run/guides/integration/a2a-integration.md`. The server (slice 2) already speaks v1.0. Today every
remote A2A call fails on this branch: `AgentCard.url` no longer exists, so `A2AClient.url` is None and
the handler raises AttributeError at `assistant_handlers.py:~1360`.

## Requirements

1. **Endpoint selection.** The client takes its URL from the card's `supportedInterfaces` (JSONRPC
   binding). `A2AClient(url=...)` still overrides it. A card with no usable interface raises a clear
   error. The handler stops reading `agent_card.url`.
2. **Discovery.** `A2ACardResolver.build_agent_json_url` (name kept) targets
   `.well-known/agent-card.json` only. A URL already ending in that path is used as is. The legacy
   `agent.json` path is no longer tried.
3. **Method and type names.** The client uses the v1.0 request and response types
   (`SendMessage`, `GetTask`, `CancelTask`, `SendStreamingMessage`). The aliases `SendTaskRequest`,
   `SendTaskResponse`, `SendTaskStreamingRequest` and `SendTaskStreamingResponse` in `a2a/types.py`
   (~269-274) are removed, and their remaining consumers (client files, `assistant_handlers.py:~53`)
   switch to the v1.0 names.
4. **Wire names.** Request bodies are dumped with `by_alias=True`, so the wire carries `taskId` and
   `contextId`. Request param models (`TaskSendParams`, `TaskIdParams`, `TaskQueryParams`) accept
   both the old and new names on input (`AliasChoices`), as `Task` already does.
5. **No discriminators.** No `kind` or `type` on parts. `_process_error_response` builds real
   `TextPart` objects instead of `{"type": "text"}` dicts.
6. **Streaming.** `send_task_streaming` uses `httpx.AsyncClient` with `httpx_sse.aconnect_sse`,
   with no blocking sync client in the async generator. The SSE parser is tolerant: it accepts both
   the server's current bare status/artifact frames and the v1.0 `statusUpdate`/`artifactUpdate`
   envelope, and yields the same internal response type. No server change.
7. **TLS.** All client HTTP calls (JSON-RPC POST, SSE, card fetch) pass `verify` from
   `config.HTTPS_VERIFY_SSL`.
8. **Version header.** Every client request, including the card fetch, sends
   `A2A-Version: <version.A2A_PROTOCOL_VERSION>` ("1.0").
9. **Push notifications removed.** `set_task_callback` and `get_task_callback` are deleted from
   `A2AClient`, along with the push-notification type usage in the client and their tests. The server
   rejects these methods with -32600.
10. **Bedrock AgentCore dropped from the A2A client.** `BedrockAdapter`, adapter selection by
    `bedrock_agentcore` and the v0.3 normalization are removed (`BaseAdapter` may be inlined). The
    SigV4 helper in `utils.get_auth_header` is kept. The card resolver and client fail fast with a clear
    error for `bedrock-agentcore` URLs (v0.3 is unsupported). A follow-up ticket is noted for
    a quarantined v0.3 adapter. `routers/assistant.py:~774` handling of `bedrock_agentcore` is left
    untouched unless it fails to compile against the resolver change.
11. **Version docs.** The `a2a/version.py` docstring drops the stale "pre-1.0 draft" wording.

## Tests

The client tests under `tests/codemie/rest_api/a2a/client/` are updated to the v1.0 shapes:
fixtures include `supportedInterfaces`, discovery uses `agent-card.json`, parts have no `type`,
streaming events cover both the bare and the enveloped frames, and the push-notification and
Bedrock tests are removed. New tests cover: URL selection from `supportedInterfaces`,
`A2A-Version` header, alias-correct request bodies, the bedrock-agentcore fail-fast, and the
handler no longer touching `agent_card.url`. Tests run in WSL Ubuntu, scoped, `-n 0`.

## Non-goals

- No server code changes (slice 2 is done), including the missing SSE envelope.
- No backward compatibility for old-shape stored `Assistant.agent_card` rows or old-shape remote
  cards (slice 4).
- No Alembic or DB changes.
- No migration to `a2a-sdk`, and no v0.3 or Bedrock AgentCore A2A support.
- No v1.0 push-notification config methods on the client.
- No changes to the separate boto-based AgentCore path (`services/aws_bedrock/*`,
  `agents/aws_bedrock/agentcore_executor.py`).
- No new client features (retries, connection pooling, auth changes).

## Acceptance criteria

- The client tests in `tests/codemie/rest_api/a2a/client/` pass, replacing the 6 failures and 23 errors.
- A remote call through `A2AAssistantHandler` reaches the URL from `supportedInterfaces` with no `agent_card.url` access.
- Requests carry `A2A-Version: 1.0`, use v1.0 method names, and serialize `taskId`/`contextId`.
- The card resolver fetches `.well-known/agent-card.json` only.
- Streaming is async, tolerates bare and enveloped SSE frames, and honors `HTTPS_VERIFY_SSL`.
- No references to the removed `SendTask*` aliases, `BedrockAdapter` or the push-notification client methods remain in `src/`.
- `bedrock-agentcore` URLs produce a clear unsupported error.
- `make ruff` is clean on changed files.

## Risks

- Existing AgentCore remotes stop working until a follow-up. This is intentional, because they are already broken.
- Old-shape stored cards still fail validation until slice 4.
