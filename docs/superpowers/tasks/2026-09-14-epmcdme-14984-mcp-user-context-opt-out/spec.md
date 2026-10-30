# Spec — EPMCDME-14984: MCP + DSP user_context opt-out

**Ticket**: EPMCDME-14984 (sub-task of EPMCDME-13260) · **Story**: `docs/stories/2026-09-14-mcp-user-context-opt-out/story.md`
(SCOPE UPDATE 2026-09-14: default flips to disabled everywhere; DSP path added.)

This replaces an earlier, MCP-only, default-`True` spec pass at this same path in full.

## Approach

Two independent `allow_user_context: bool = False` gates, one per path, sharing only a field name
for developer familiarity — no shared resolution code (YAGNI; DSP has no override concept to unify
with MCP's).

### MCP path — two-level precedence, unchanged design from the discarded pass except default

1. `MCPServerConfigData.allow_user_context: bool = Field(default=False, ...)` —
   `src/codemie/rest_api/models/mcp_config.py:97`, the catalog (server-level) default, matching the
   `single_usage`/`allow_issuer_prefix_match` style already on this model.
2. Same name, `bool = False`, mirrored onto the runtime `MCPServerConfig` —
   `src/codemie/service/mcp/models.py:200`. `MCPAccessControlService.resolve_catalog_config()`
   (`access_control.py:180`) does `MCPServerConfig(**entry.config.model_dump())`; matching field
   names is the entire mechanism that carries the catalog default across — no extra plumbing.
3. `MCPServerDetails.allow_user_context: Optional[bool] = Field(default=None, ...)` —
   `src/codemie/rest_api/models/assistant.py:174`, **top-level, a sibling of `config`, not nested
   inside it**. `resolve_catalog_config()` (`access_control.py:159-187`) does
   `mcp_server.model_copy(update={"config": resolved_config})` for catalog-referenced servers,
   wholesale replacing `config` — anything nested inside it would be silently discarded before
   reaching the request builder. A sibling field survives untouched. `None` = no assistant opinion.
4. Precedence resolved once, in `MCPToolkitService._build_mcp_server_config()`
   (`toolkit_service.py:1514-1569`), after `actual_config: MCPServerConfig` is built (already
   carrying the resolved server default): if `mcp_server.allow_user_context is not None`, it
   overwrites `actual_config.allow_user_context`; otherwise the server default stands. Every
   downstream consumer of the returned `MCPServerConfig` then sees one final, resolved bool.
5. Enforcement in `MCPConnectClient._build_request_payload()` (`client.py:100-129`): the fields
   from `execution_context.to_request_fields()` are unpacked into `MCPToolInvocationRequest`
   unchanged; when `server_config.allow_user_context` is `False`, drop `user_context` from that
   unpacked dict before constructing the request. `server_config` is already per-server here, so
   this needs no new per-server context plumbing. Applies to both `list_tools()` and `invoke_tool()`
   since both call this method. `MCPExecutionContext`'s construction (`toolkit_service.py:185-244`,
   still built once per `get_mcp_server_tools()` call, not per-server) and
   `to_request_fields()`'s own `exclude=...`/`extra_attributes` mechanics (`models.py:167-193`) are
   both untouched — the gate acts on `_build_request_payload()`'s output, not on how the shared
   context is built or serialized.
6. `MCPConnectClient.invoke_tool()`'s masked debug log (`client.py:239-243`, currently
   `if execution_context is not None and execution_context.user_context is not None`) must also
   require `server_config.allow_user_context`, so it only fires when `user_context` is actually
   being sent, not merely present on the context object.
7. Add `"allow_user_context"` to `_CATALOG_REF_ALLOWED_FIELDS`
   (`access_control.py:26-39`, checked by `_validate_restricted_mode`, `access_control.py:57-69`) so
   the assistant-level override remains settable on a catalog-referenced server when
   `mcpCustomServersDisabled` restricted mode is active.

### DSP path — new, flat single-level gate

1. `ProviderBase.allow_user_context: bool = Field(default=False, ...)` —
   `src/codemie/rest_api/models/provider.py:255-259`. Confirmed no per-assistant or per-datasource
   override concept exists anywhere in the DSP model family (`ProviderToolkitsFactory`/
   `ProviderToolFactory` are built from a single `Provider` row with no assistant identity threaded
   through) — this is intentionally flat, not a precedence design, and no resolution utility is
   built for it.
2. `CreateProviderRequest` and `UpdateProviderRequest` (`provider.py:279-322`) are standalone
   `BaseModel`s, not `ProviderBase` subclasses — the field must be added to both explicitly:
   `CreateProviderRequest.allow_user_context: bool = Field(default=False, ...)` (concrete, matches
   `Provider(**request.dict(...))` in `ProviderService.create()`) and
   `UpdateProviderRequest.allow_user_context: Optional[bool] = Field(default=None, ...)` (`None` =
   not provided; needed by the update fix below).
3. Enforcement in `ProviderToolFactory.invoke()` (`provider_tool_factory.py:131-177`), reading
   `self.provider_config.allow_user_context` (already in scope): gate both the
   `user_context=UserContext.from_user(user).model_dump(exclude_none=True)` kwarg on
   `ToolInvocationRequest` (line 175) and the masked debug log (line 177) behind the same
   `if self.provider_config.allow_user_context:` check.
4. **`ProviderService.update()` fix** (`provider_service.py:54-66`): its current field-copy loop,
   `for field, value in request.dict().items(): if value: setattr(provider, field, value)`, only
   applies truthy values, so an explicit `allow_user_context=False` sent in an update is silently
   dropped — a provider could be enabled but never re-disabled via the API. Special-case only this
   field (leave the loop's behavior for every other field unchanged):
   ```python
   provided = request.dict(exclude_unset=True)
   for field, value in request.dict().items():
       if value or (field == "allow_user_context" and field in provided):
           setattr(provider, field, value)
   ```
5. Default `False` is a live, accepted behavior change to DSP's current unconditional send —
   stakeholder-approved (no known consumer depends on it); not gated on further sign-off.

## Acceptance criteria

MCP:
- Server with no explicit `allow_user_context` anywhere: `user_context` omitted (default off).
- Server catalog default explicitly `True`, no assistant override: `user_context` sent, same shape
  as EPMCDME-13260 (`extra_attributes` still excluded, masked in logs).
- Assistant override `True` on a server whose default is `False`: sent.
- Assistant override `False` on a server whose default is `True`: omitted.
- `masked_for_logging()` in `invoke_tool()` fires only when `user_context` is actually sent.
- Restricted mode (`mcpCustomServersDisabled`) still allows setting the per-assistant override on a
  catalog-referenced server.

DSP:
- Provider with no explicit `allow_user_context`: `user_context` omitted (default off, reversing
  today's unconditional send).
- Provider with `allow_user_context` explicitly `True`: `user_context` sent, same shape as today.
- Provider previously enabled, then updated to `allow_user_context: False`: `user_context` becomes
  omitted (exercises the `ProviderService.update()` fix).
- DSP masked debug log fires only when `user_context` is actually sent.

## Non-goals

- No change to MCP-Connect (`codemie-mcp-connect-service`, branch EPMCDME-14847) — it keeps
  treating `user_context` as opaque; enforcement is entirely backend-side.
- No change to what `user_context` contains when sent, on either path — the `extra_attributes`
  exclusion (MCP) and DSP's equivalent shape are untouched; this is only about whether it's sent.
- No UI for either toggle — backend config + enforcement only.
- No shared precedence-resolution utility across MCP and DSP — the two gates stay independent;
  `allow_user_context` is a shared name, not a shared mechanism.
- No change to `MCPExecutionContext`'s per-call (not per-server) construction in
  `get_mcp_server_tools()` — the MCP gate operates entirely on the already-per-server
  `server_config`.
- No broader fix to `ProviderService.update()`'s truthy-check semantics for any field other than
  `allow_user_context`.
- No migration/backfill on either path — all new fields carry safe defaults and are additive to
  JSONB-backed Pydantic models. (DSP's default flip is a live behavior change, not a migration
  concern — see acceptance criteria.)

## Open risks

- The `MCPServerConfig(**entry.config.model_dump())` crossover (`access_control.py:180`) depends on
  `MCPServerConfigData` and `MCPServerConfig` keeping identical field names; the implementer must
  not let the two names drift or the server-level default silently stops propagating.
- `_validate_restricted_mode()` treats any non-`None` `MCPServerDetails` field not in
  `_CATALOG_REF_ALLOWED_FIELDS` as a violation; since the new override field defaults to `None`,
  pre-existing servers are unaffected, but restricted-mode behavior with an explicit override set
  should get test coverage.
- DSP's default flip to `False` is a live behavior change for any assistant/tool currently relying
  on `user_context` from DSP; accepted by stakeholder decision per the story's scope update, not an
  open question, but worth surfacing in the MR description for reviewer awareness.
