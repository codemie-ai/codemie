# Spec: A2A backward compatibility (EPMCDME-15327, slice 4)

## Problem
Slices 2 and 3 moved the server and client to A2A v1.0. `AgentCard.supportedInterfaces` (`src/codemie/rest_api/a2a/types.py`, ~l.368) is now required and `url` is gone. Stored pre-v1.0 cards (`url`, no `supportedInterfaces`) are validated in `PydanticType.process_result_value` (`rest_api/models/base.py:280`). Loading any assistant, assistant version or clone that carries such a card therefore fails at ORM load time, not only at chat time. v0.3 remote cards fetched by the client fail the same way. `get_jsonrpc_url` raises a bare `ValueError` that surfaces as a 500 (`handlers/assistant_handlers.py:~1367`). Old-shape clients get an unexplained -32600.

## Approach
Read-time normalisation only; no data migration. Rows self-heal on the next save. Chosen because it is smaller, testable without Postgres, and fixes every read path (assistants, versions, clone, remote fetch) in one place.

## Requirements
1. `AgentCard` has a `model_validator(mode="before")`. If input has `url` and no non-empty `supportedInterfaces`, it synthesises `supportedInterfaces=[{url, transport: "JSONRPC"}]`. Input that already has `supportedInterfaces` is untouched (the v1.0 value wins). `url` is not re-emitted on dump.
2. The validator applies to every `AgentCard` validation, including the client card resolver. v0.3 remote cards carrying `url` are accepted by the client. The `bedrock-agentcore` URL fail-fast from slice 3 is unchanged, so Bedrock v0.3 stays unsupported.
3. `get_jsonrpc_url` raises a typed exception (a subclass of `ValueError`, so existing callers still work) when the card has no JSON-RPC interface. The A2A handler/router maps it to a client-error JSON-RPC response (not a 500), following `.ai-run/guides/development/error-handling.md`.
4. The server keeps returning -32600 for v0.3 method names (`message/send`, `tasks/get`, and so on). The error message gains a hint that the supported A2A version is 1.0. No method translation and no `A2A-Version` header handling are added.
5. No `.well-known/agent.json` route is added; only `agent-card.json` is served.
6. Legacy `a2a_tasks` rows (NULL `assistant_id`/`user_id`) keep failing closed: `GetTask` and `CancelTask` return not-found through the existing ownership check (`codemie_task_manager.py:~129`). No migration and no back-fill, because the owner was never recorded. Legacy JSONB parts with `kind` and legacy `sessionId` must still parse. Document this behaviour in `.ai-run/guides/integration/a2a-integration.md`.
7. Fix the slice-2 regression in `tests/codemie/rest_api/handlers/test_assistant_handlers_streaming.py` (~l.521), which builds an `AgentCard` with `url` and no `supportedInterfaces`. Requirement 1 fixes it without editing the test; verify it and adjust only if needed.

## Acceptance criteria
- AC1: `AgentCard.model_validate` of a `url`-only card yields one JSONRPC interface with that url.
- AC2: A card with both `url` and `supportedInterfaces` keeps its `supportedInterfaces` unchanged.
- AC3: A legacy stored card loads through `PydanticType` (Assistant read) without error.
- AC4: The client card resolver accepts a v0.3 remote card with `url`.
- AC5: A card with no JSON-RPC interface produces the typed exception, and the router returns a 4xx / JSON-RPC error instead of a 500.
- AC6: A v0.3 method name returns -32600 with a message naming supported version 1.0.
- AC7: A legacy `a2a_tasks` row with NULL owner yields not-found; its `kind`-bearing parts parse.
- AC8: The streaming test at `test_assistant_handlers_streaming.py:~521` passes. Tests are run in WSL with scoped paths and `-n 0`.
- AC9: The guide documents the compat behaviour.

## Non-goals
- No Alembic migration and no data migration of `agent_card` or `a2a_tasks`.
- No translation of v0.3 method names, and no `A2A-Version` header negotiation on the server.
- No `agent.json` route.
- No back-fill of, or relaxation of, task ownership checks.
- No Bedrock AgentCore v0.3 support and no `BedrockAdapter`.
- No change to the accepted gaps: -32600 for out-of-scope methods, no `statusUpdate`/`artifactUpdate` envelope on server SSE, and cancel only flips stored status.
- No change to `Part` shape or the client beyond what requirement 2 implies.

## Risks
- The load-time failure was found by reading the code and has not been reproduced. Tests in AC3 cover it.
- The `c9d8e7f6a5b4` migration from slice 2 is still unverified because no Postgres is available. This slice adds no migration.
