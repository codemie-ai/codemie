# Technical Research

**Task**: a2a backward-compat agent-card migration
**Generated**: 2026-09-30
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15327 slice 4 a2a-backward-compat: add backward compatibility for legacy (pre-v1.0) stored agent cards, a2a_tasks rows and old-shape A2A clients per .ai-run/guides/integration/a2a-integration.md; server (slice 2) and client (slice 3) are done.

(Known inputs 1-5 and accepted gaps as listed in the request and in `docs/superpowers/tasks/2026-09-30-a2a-client-protocol/HANDOVER-slice-4.md`; not re-litigated.)

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/a2a/types.py`
  - `AgentCard` (l.368): `supportedInterfaces: List[AgentInterface]` is **required**, no `url` field, no default. `bedrock_agentcore: bool = False`. Pydantic default `extra` is ignore, so a legacy `url` key is silently dropped.
  - `AgentInterface{url, transport}`.
  - `get_jsonrpc_url` (l.386) raises bare `ValueError("Agent card has no JSON-RPC interface")`.
  - `A2ARequest` discriminated union (l.261) over exactly 5 methods: `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `GetExtendedAgentCard`. Anything else fails validation and becomes -32600.
  - Old push-notification request classes (`tasks/pushNotification/set|get`) still exist but are not in the union.
  - `Part = Union[TextPart, FilePart, DataPart]` with no discriminator and no `kind`. Legacy `kind` keys are ignored on parse.
  - `Task` (table `a2a_tasks`): `contextId`, `sessionId`, `assistant_id`, `user_id` (`exclude=True`), and JSONB `status`/`artifacts`/`history` via `PydanticType`/`PydanticListType`.
  - `TaskSendParams`/`TaskIdParams` accept both new and old names (`taskId`/`id`, `contextId`/`sessionId`) via `AliasChoices`.
- `src/codemie/rest_api/models/base.py:280` `PydanticType.process_result_value` calls `pydantic_type.model_validate(value)` with no fallback.
- `Assistant.agent_card` is `PydanticType(AgentCard)`: `models/assistant.py:698` (table), `:1295` (versions table), `:361` (request model). It is also copied in `service/assistant/assistant_version_service.py`, `assistant_version_migration_service.py` and `assistant_version_compare_service.py`.
- `src/codemie/rest_api/handlers/assistant_handlers.py:1331` `A2AAssistantHandler` takes `assistant.agent_card`; `:1367` calls `get_jsonrpc_url(self.agent_card)` in `process_request`. A `ValueError` here is not mapped to a typed error.
- `src/codemie/rest_api/routers/a2a.py`
  - Routes: `GET /assistants/{id}/.well-known/agent-card.json`, `POST /assistants/{id}` (dispatch table for 4 methods plus SSE), `GET /assistants/fetch`. Errors go through `_handle_exception`.
  - No `agent.json` route.
  - No `A2A-Version` header read on the server (grep of `src/` finds it only in client `client.py:180` and `card_resolver.py:86`).
- `routers/assistant.py:774` uses only `request.agent_card.bedrock_agentcore` to set the origin. The `/assistants/fetch` path is in `routers/a2a.py`, not `assistant.py`.
- `service/settings/settings.py` has no `agent_card`/`AgentCard` reference; nothing to change there.
- `codemie_task_manager.py:129` ownership check `task.assistant_id == self.assistant.id and task.user_id == self.user.id`. Legacy rows (NULL owner) therefore fail this check and are inaccessible (fail-closed). Task creation goes through `upsert_task` (l.150+).

### Architecture and Layers Affected
Data model and persistence (`types.py`, `PydanticType`, JSONB `assistants.agent_card`, `a2a_tasks`), Alembic migrations, the assistant handler layer, the A2A router (server compat), and the version-service copy paths. The client and card resolver are already done.

### Integration Points
- Assistant CRUD, versioning and clone paths copy `agent_card` as an object, so any normalisation in `AgentCard` runs on every load and re-save.
- Remote card fetch (`card_resolver.fetch_agent_card`) sends `A2A-Version: 1.0` and validates the result as `AgentCard`. A v0.3 remote card (with `url`) would fail validation the same way.
- Guide `a2a-integration.md` says "empty header = 0.3" (l.18) but defines no old-client translation, no `agent.json` requirement and no `kind` requirement. Those are spec/design decisions, not documented requirements.

### Patterns and Conventions
- Pydantic v2 `model_validator` and `AliasChoices` are already used for old-name input. A `model_validator(mode="before")` on `AgentCard` mapping `url` to `supportedInterfaces` fits this pattern.
- Alembic files use string revision ids. Head is `c9d8e7f6a5b4` (single head; no other revision names it as down_revision). It descends from `u2v3w4x5y6a7`. Existing a2a migrations: `074efb996aac` (agent_card column), `8f68ee6d23b6` (a2a_tasks).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/integration/a2a-integration.md` is the only relevant guide. It fixes v1.0 markers and names this slice as a consumer. It lists no concrete compat requirements beyond the empty-header-means-0.3 note.

### Architectural Decisions
- The guide decides on an in-house upgrade; `BedrockAdapter` is not a reference.
- Slice 3 decision: Bedrock AgentCore v0.3 dropped. URLs containing `bedrock-agentcore` fail fast with `ValueError`.
- Accepted gaps (-32600, SSE envelope, cancel) are out of scope.

### Derived Conventions
Follow the handover: every commit is prefixed `EPMCDME-15327:`, and implementation goes through the implementer agent.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/rest_api/a2a/` (`test_types.py`, `test_utils.py`, `client/*`), `routers/test_a2a_router.py`, `handlers/test_a2a_assistant_handler.py`, and `handlers/test_assistant_handlers_streaming.py`.

### Testing Framework and Patterns
pytest with mocks (`Mock`, `patch.object`, `SimpleNamespace`). Run in WSL with scoped paths and `-n 0` (native Windows venv is broken).

### Coverage Gaps
- No test loads a legacy-shape (`url`-only) card through `PydanticType`/`Assistant`.
- No test for legacy `a2a_tasks` row parsing.
- No server test for old-method / no-header behaviour.
- No migration test (no Postgres available).

### Regression check (input 6)
`test_assistant_handlers_streaming.py:521` `test_handle_a2a_stream_finalizes_stale_in_progress_thought_on_success` builds `AgentCard(..., url="https://remote-agent.example.com/api", ..., skills=[])` with no `supportedInterfaces`. On `main`, `AgentCard.url: str` was required (`types.py` l.342) and there was no `supportedInterfaces`, so the test was valid there. On this branch `supportedInterfaces` is required, so construction raises `ValidationError`. **Determined by static reading; the test was not executed.** It is a slice-2 regression. The test needs updating (or the model needs a `url` before-validator, which would fix it as a side effect).

---

## 5. Configuration and Environment

### Environment Variables
None found specific to this area.

### Configuration Files
`src/external/alembic/versions/` for migrations. No feature flags found for A2A.

### Feature Flags and Deployment Concerns
`c9d8e7f6a5b4` adds nullable `contextId`, `assistant_id`, `user_id` columns and two indexes. `downgrade` reverses them. It was never run on a real DB. Any new revision must chain from `c9d8e7f6a5b4` with a fresh id (no collision found in the versions dir).

---

## 6. Risk Indicators

- **Load-time failure, not chat-time.** Because `PydanticType.process_result_value` calls `AgentCard.model_validate` and `supportedInterfaces` is required, a stored v0.3 row (`url`, no `supportedInterfaces`) raises `ValidationError` when the `Assistant` row is loaded. The 500 is therefore likely earlier than `get_jsonrpc_url` at `assistant_handlers.py:1367`. This affects list, get, version and clone reads of any assistant that has a legacy card, and is broader than the handover states. It was determined from code reading and not reproduced.
- Read-time normalisation is the smallest fix: a `mode="before"` validator on `AgentCard` that synthesizes `supportedInterfaces=[{url, transport:"JSONRPC"}]` from `url`. It also fixes the streaming test and v0.3 remote card fetches, but the legacy `url` is dropped on the next re-save. That is acceptable, since rows self-heal on save. A data migration is the alternative; it needs a JSONB update over `assistants` and `assistant_versions` (`agent_card` at `772de5bd70b1`) and cannot be verified without Postgres.
- Whichever option is chosen, `get_jsonrpc_url`'s bare `ValueError` still reaches users for bedrock and empty-interface cards. Speculative: a typed exception mapped to a 4xx would be needed.
- Legacy `a2a_tasks` rows: JSONB parts with `kind` parse fine (extras ignored); `sessionId` remains. Owner columns are NULL, so `GetTask`/`CancelTask` return not-found for them (fail-closed by the ownership check). Speculative: this may be acceptable and need no migration; back-filling owners is not possible because the owner is not recorded.
- Old clients: the server does not read `A2A-Version`. Old method names (`message/send`, `tasks/get`) get -32600. Any translation is a new-scope design decision, since the guide only mandates the empty-header-means-0.3 interpretation. A missing `agent.json` route returns 404.
- Re-introducing Bedrock support is prohibited without an explicit decision (input 4).
- The migration remains unverified; a second revision adds chain risk.
- The streaming-test regression touches a pre-existing test file outside the a2a test folders.

---

## 7. Summary for Complexity Assessment

The change concentrates in `a2a/types.py` (AgentCard normalisation, error typing), the A2A router (optional old-client handling), the handler at `assistant_handlers.py:1367`, and possibly one Alembic revision. The smallest viable path is a before-validator in `AgentCard` plus a typed error, touching roughly 3 to 5 source files and about 3 test files. Scope grows if the user wants a data migration or old-method translation on the server.

Technical novelty is low: it reuses existing Pydantic alias and validator patterns. The main uncertainty is that the failure surface is wider than assumed, because legacy cards fail at ORM load time. Test coverage of the legacy shapes is nil, and migrations cannot be verified locally because no Postgres is available.

Key risks are the ORM load-time failure, the unverified `c9d8e7f6a5b4` migration, the ambiguous guide guidance on old clients (decisions needed from the user), and the slice-2 regression in the streaming test.

---

## 8. External References

None named by the task.
