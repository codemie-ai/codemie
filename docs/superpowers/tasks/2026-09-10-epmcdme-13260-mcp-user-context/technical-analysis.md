# Technical Research

**Task**: mcp user-context serialization masked-logging
**Generated**: 2026-09-10
**Research path**: codegraph

---

## 1. Original Context

task_context: EPMCDME-13260 — finish the MCP path of user_context propagation in the codemie backend.

Full brief is at the repo root: `EPMCDME-13260-backend-task.md` (read it — it contains verified file:line references, the contract, and the exact changes). Summary of the work:

1. Re-enable the `user_context` field on the MCP wire. Today it is populated on `MCPExecutionContext` (src/codemie/service/mcp/toolkit_service.py:243), declared on both models (src/codemie/service/mcp/models.py:156 and :504), then stripped in `MCPExecutionContext.to_request_fields()` via the exclude set at src/codemie/service/mcp/models.py:185. EPMCDME-13546 added that exclusion because MCP-Connect's `/bridge` model was `extra="forbid"`. The counterpart fix (EPMCDME-14847) that makes the bridge accept the field exists locally but is not yet deployed. The change here: delete `"user_context",` from the exclude set and rewrite BOTH docstrings that cite EPMCDME-13546 as justification — the field description at models.py:156-163 and the method docstring at models.py:165-176. `exclude_none=True` at src/codemie/service/mcp/client.py:128 already drops the key when context is None, so no extra guard is needed.

2. Masked logging. `masked_for_logging()` / `_mask_value()` / `_mask_email()` were written on the stale `origin/EPMCDME-13260` branch (241 commits behind main) and never merged — zero matches in src/ today. Re-implement on current main (~40 lines), do NOT cherry-pick. Apply at BOTH log sites: src/codemie/service/mcp/client.py (MCP invocation) and src/codemie/service/provider/provider_tool_factory.py:175 (DSP path, which ships user_context today and logs it unmasked). Mask: email (-> j***@epam.com), name, picture, username (partial). Keep readable: id, roles, is_admin, is_maintainer, is_auditor, user_type, project_names, admin_project_names.

3. Tests. tests/codemie/service/mcp/test_models.py has 5 assertion sites locking the exclusion (lines 87, 107, 140, 190, 207) plus two dedicated tests (test_to_request_fields_excludes_user_context_regardless_of_value at :177, test_to_request_fields_never_includes_user_context at :197). Delete the two dedicated tests; replace with positive coverage (user_context present when set, absent when None via exclude_none, auth_token never in UserContext, masked_for_logging behaviour).

4. Tool-owner documentation (brief section 8) — nothing exists under docs/. IN SCOPE for this task. Must cover the `_meta` key `codemie.ai/user-context`, the field list (all optional), backend-populated / not tool-injectable, transport-level trust (not signed), no credentials, PII obligations, per-call snapshot.

OUT OF SCOPE (user decision, pending a DM discussion): the brief's section 5 — do NOT drop `extra_attributes` from `UserContext.from_user()` (src/codemie/rest_api/security/user.py:165), and do NOT add `is_auditor` to the SPI schema. The MCP path mirrors current DSP behaviour: forward `user_context` (including `extra_attributes`) exactly as the DSP path already does.

feature_area: mcp user-context serialization masked-logging

run_dir: docs/superpowers/tasks/2026-09-10-epmcdme-13260-mcp-user-context

Focus the analysis on: the MCPExecutionContext / MCPToolInvocationRequest models and to_request_fields; the single request construction + serialization site (client.py:123-128); how the DSP path (provider_tool_factory.py) currently serialises and logs user_context so the MCP path can mirror it; the UserContext model (rest_api/security/user.py:127) and its fields; existing logging patterns in these modules; and the full set of existing tests that touch user_context / MCPExecutionContext serialization so the plan knows every assertion that must change. Note switchyard commit 91a7f49fa only made cosmetic string-format edits to toolkit_service.py — no functional collision.

---

## 2. Codebase Findings

### Existing Implementations

- **`src/codemie/service/mcp/models.py`**
  - `MCPExecutionContext` (class, line 87) — request-scoped, non-cached context object. Field `user_context: UserContext | None` declared at **line 156-163**; description text currently cites EPMCDME-13546 as the reason it is excluded from `to_request_fields()`.
  - `MCPExecutionContext.to_request_fields()` (method, line 165-187) — the only transform from context to wire fields. Calls `self.model_dump(exclude={...})`. The exclude set (lines 179-186) contains `auth_headers`, `conversation_id`, `oauth2_token_data`, `oauth2_auth_config_id`, `oauth2_auth_config`, `user_context`. Method docstring lines 165-177 also cites EPMCDME-13546. **`to_request_fields()` does NOT pass `exclude_none`** — so any field not in the exclude set is emitted even when `None`.
  - `MCPToolInvocationRequest` (class, line 473) — the outbound wire model. `user_context: UserContext | None` already declared at **line 504-508** with a neutral description ("Optional; absent on legacy requests."). No `extra="forbid"` on this model. Sibling context fields: `user_id`, `assistant_id`, `project_name`, `workflow_execution_id`, `request_headers` (lines 497-503).
- **`src/codemie/service/mcp/client.py`** — `MCPConnectClient`
  - `_build_request_payload()` (line 100-129) — the single request construction site. Builds `MCPToolInvocationRequest(...)` and splats `**execution_context.to_request_fields()` (line 123). Then `post_body = request.model_dump(exclude_none=True)` (line 128). Because of `exclude_none=True` here, a `user_context=None` coming out of `to_request_fields()` is dropped before the wire.
  - Used by both `list_tools()` (line 131, `method="tools/list"`) and `invoke_tool()` (line 203, `method="tools/call"`). Both call `_build_request_payload`.
  - Logging in this module uses `from codemie.configs.logger import logger`. Existing invocation log lines: `invoke_tool` logs tool name + `server_config.command`/`args` (lines 237-238); `list_tools` logs command/args at debug (lines 160-163). **No log line currently emits execution-context or user fields.** Error paths log status code + reason + truncated response text (lines 177-178, 259).
- **`src/codemie/service/mcp/toolkit.py`** — `MCPTool._aexecute_with_context()` (line 139) forwards `execution_context` into `mcp_client.invoke_tool(...)`. `MCPTool` extends `CodeMieTool`.
- **`src/codemie/service/mcp/toolkit_service.py`** — line 234-244: `MCPExecutionContext(...)` is constructed with `user_context=UserContext.from_user(current_user) if current_user else None` (line 243). `current_user = get_current_user()` (line 233). This is the population site; no change needed here. Brief also notes a pre-existing env user-context injection at line 1696 — explicitly leave alone.
- **`src/codemie/rest_api/security/user.py`**
  - `UserContext` (class, line 127) — "Non-sensitive user profile forwarded to downstream tool invocations (DSP and MCP)." 14 optional fields: `id`, `username`, `name`, `email`, `roles`, `is_admin`, `is_maintainer`, `is_auditor`, `user_type`, `project_names`, `admin_project_names`, `knowledge_bases`, `picture`, `extra_attributes: dict[str, Any] | None`.
  - Docstring states it "Never includes credentials or internal identifiers (auth_token, tenant_id)."
  - `UserContext.from_user()` (classmethod, line 150-166) — maps 14 fields from `User`, including `extra_attributes=user.extra_attributes` (line 165). No `auth_token` / `tenant_id` / `client_access_token` / `project_limit` copied.
  - `User` (class, line 30) — `auth_token`, `client_access_token`, `tenant_id` are all `Field(..., exclude=True)`; `extra_attributes: dict[str, Any] | None = None` (line 56) is NOT excluded.
  - Blast radius: `UserContext` has 11 direct callers (models.py, provider_tool_factory.py, toolkit_service.py + tests); `from_user` has 12. `User` is referenced 1000+ places but the model shape is not being changed.
- **`src/codemie/service/provider/provider_tool_factory.py`** — DSP path, the mirror reference
  - `ProviderToolFactory.invoke()` (line 131-198) builds `ToolInvocationRequest(..., user_context=UserContext.from_user(user).model_dump(exclude_none=True))` at **line 175**. So DSP serialises `user_context` as a plain dict via `exclude_none=True`.
  - Logging: `from codemie.configs import logger`. Log lines in `invoke()`: `logger.info(f"{log_prefix} Invoking tool")` (line 182), `logger.info(f"{log_prefix} Invoked tool successfully")` (line 190), `logger.warning`/`logger.error` on failure (lines 194, 197). **No line currently prints `user_context` or `tool_invocation_request` contents** in this function; the brief's "logs it unmasked" refers to the absence of any masking utility on this path plus the risk that request bodies are logged at the client/API-client boundary. There is no `_mask_*` helper anywhere in `src/` (confirmed — zero matches).
- **`src/codemie/clients/provider/client/models/tool_invocation_request.py:43`** — DSP wire model; per brief, `user_context` there is an untyped dict (`Optional[Dict[str, Any]]`), whereas the MCP wire model (`MCPToolInvocationRequest.user_context`) is the typed `UserContext | None`. Minor shape asymmetry; both serialise to the same JSON.

### Architecture and Layers Affected

- **Domain / wire models layer** — `src/codemie/service/mcp/models.py` (`MCPExecutionContext.to_request_fields`, field + method docstrings). This is the entire production behaviour change for item 1.
- **Service / integration layer (MCP)** — `src/codemie/service/mcp/client.py` (`MCPConnectClient`, `_build_request_payload` / `invoke_tool`). New masked log line, item 2. Serialization boundary at `client.py:128` already correct.
- **Service / integration layer (DSP)** — `src/codemie/service/provider/provider_tool_factory.py` (`ProviderToolFactory.invoke`, line 175). New masked log line, item 2.
- **Security / identity layer** — `src/codemie/rest_api/security/user.py` (`UserContext`). Read-only for this task per the out-of-scope note; the new masking helper consumes its field set.
- **Shared logging utility** — a new `masked_for_logging()` / `_mask_value()` / `_mask_email()` helper (~40 lines). No existing home; candidates observed: `src/codemie/service/mcp/` (co-located with client), `src/codemie/rest_api/security/` (co-located with `UserContext`), or `src/codemie/configs/logger.py` (existing `LogFormatter` / `LoggingContextSnapshot`). Placement is a design decision.
- **Test suite** — `tests/codemie/service/mcp/test_models.py`, `tests/codemie/service/mcp/test_models_1_2_auth_config.py`, `tests/codemie/rest_api/security/test_user_context.py`, `tests/codemie/service/provider/test_provider_tool_factory.py`, and (read-only verify) `tests/codemie/service/mcp/test_client.py`.
- **Developer documentation** — `docs/` (no MCP/tool-owner subtree exists today).

### Integration Points

- `toolkit_service.py` → constructs `MCPExecutionContext` (with `user_context`) → `MCPTool._aexecute_with_context` → `MCPConnectClient.invoke_tool` / `list_tools` → `_build_request_payload` → `to_request_fields()` + `model_dump(exclude_none=True)` → HTTP POST to MCP-Connect `/bridge`.
- MCP-Connect `/bridge` is the external boundary. Its request model was `extra="forbid"` (EPMCDME-13546); the counterpart change EPMCDME-14847 to accept `user_context` "exists locally but is not yet deployed" (per brief). Deploy ordering is a hard sequencing constraint (brief section 6): the bridge must accept the field in production before this backend change ships, or EPMCDME-13546 recurs platform-wide.
- Bucket routing: `client.py:85-98` (`_get_actual_bridge_endpoint_url`, `MCP_CONNECT_BUCKET_PLACEHOLDER`) — bridge runs as a multi-bucket StatefulSet; mid-rollout some buckets run the old image.
- DSP path: `provider_tool_factory.invoke()` → `ProviderAPIClient` → generated `ToolInvocationManagementApi.invoke_tool` → external provider (`epam_ai_run` SPI). Already ships `user_context` today.
- `get_current_user()` (context var) is the source of the `User` that becomes `UserContext`.

### Patterns and Conventions

- All wire models are Pydantic `BaseModel`. Local-only fields use `Field(exclude=True)` (see `auth_headers`, `conversation_id`, `oauth2_*` on `MCPExecutionContext`) — an alternative to the `to_request_fields()` exclude set. `user_context` currently sits only in the exclude set, not `Field(exclude=True)`, which is why removing it from the set fully re-enables it.
- Serialization for downstream tool payloads consistently uses `.model_dump(exclude_none=True)` (DSP `provider_tool_factory.py:175`, MCP `client.py:128`).
- Logging: module-level `logger` from `codemie.configs.logger` (MCP) or `codemie.configs` (DSP). Guide `logging-patterns.md` mandates: never log raw headers/tokens/credentials; log operation + IDs + status + sanitised details; one actionable message with context; match severity to impact.
- `UserContext.from_user()` is a plain field-copy classmethod; tests assert field-by-field mapping and that sensitive `User` fields never appear in `model_dump()` / `model_dump_json()`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` — covers MCP config/auth router boundaries and init-timeout env vars only. No content on `user_context`, `_meta`, or the `/bridge` payload contract.
- `.ai-run/guides/development/logging-patterns.md` — directly relevant to item 2. "Avoid logging raw request headers, tokens, or provider credentials; prefer operation, IDs, status, and sanitized details." Severity guidance: error only for server failures, warning/structured for expected client-side issues.
- `.ai-run/guides/testing/testing-patterns.md` — tests mirror `src/` under `tests/codemie/<package path>/` and reuse local fixtures; run the narrowest scope; "Seam Tests for Policy Helpers": a helper that encodes a decision needs a per-branch test at each callsite observing the boundary value (SDK call / emitted request / constructed object), not just an isolated unit test. Red flag: deleting a callsite guard leaves tests green.
- `.ai-run/guides/development/security-patterns.md`, `.ai-run/guides/development/configuration-patterns.md` — listed by AGENTS.md as P0/P1 for the Security category; not read in depth this pass.
- No guide describes a PII-masking convention or an existing redaction helper.

### Architectural Decisions

- EPMCDME-13546 (Closed) — disabled the MCP `user_context` path by adding it to the `to_request_fields()` exclude set because MCP-Connect `/bridge` was `extra="forbid"`. EPMCDME-13260 was reopened because that left the story half-delivered.
- EPMCDME-14587 (commit `0b260a14c`, current HEAD) — "Fold OAuth credential types into base integration types" — touched MCP integration types; no conflict noted.
- Switchyard commit `91a7f49fa` — cosmetic string-format edits to `toolkit_service.py` only; no functional collision (confirmed by task).
- Related design/plan artifacts under `docs/superpowers/`: `plans/2026-08-11-oidc-extra-attributes-passthrough.md` + `specs/2026-08-11-oidc-extra-attributes-passthrough-design.md` (origin of `extra_attributes`; brief section 5 territory — out of scope here), and `plans/2026-05-15-dsp-header-propagation.md` (DSP propagation background). These are process docs, not tool-owner guidance.

### Derived Conventions

- Because there is no masking helper and no guide for one, the mask format is defined only by the brief: `email -> j***@epam.com`, `name` / `picture` masked, `username` partially masked; `id`, `roles`, `is_admin`, `is_maintainer`, `is_auditor`, `user_type`, `project_names`, `admin_project_names` kept readable. `knowledge_bases` and `extra_attributes` are not named in the brief's keep-readable list — their masking treatment is unspecified.
- Tool-owner documentation has no established location or format; `docs/` holds ad-hoc reference files (`jwks-configuration-reference.md`, `webhook-configuration.md`) plus the `docs/superpowers/` process tree. New doc placement is a design choice.

---

## 4. Testing Landscape

### Existing Coverage

- **`tests/codemie/service/mcp/test_models.py`** — `TestMCPExecutionContext`:
  - `test_to_request_fields_all_none` (line 75-87) — asserts `fields == {exact 5-key dict}` then `assert "user_context" not in fields` (line 87).
  - `test_to_request_fields_all_set` (line 89-107) — exact-dict equality + `"user_context" not in fields` (line 107).
  - `test_to_request_fields_partial` (line 125-140) — exact-dict equality + `"user_context" not in fields` (line 140).
  - `test_conversation_id_is_local_only_retry_context` (line 109-123) — asserts `conversation_id` / `auth_headers` excluded; does not touch `user_context`.
  - `test_empty_strings_vs_none` (line 158-175) — per-key assertions, no exact-dict equality, no `user_context` assertion.
  - `test_to_request_fields_excludes_user_context_regardless_of_value` (line 177-195) — dedicated; builds `UserContext(id=..., email=...)`, asserts `"user_context" not in fields` (line 190) + field still readable on the object.
  - `test_to_request_fields_never_includes_user_context` (line 197-212) — dedicated; `"user_context" not in fields` (line 207) + readable on object.
  - Other classes in the file (`TestMCPServerConfig*`, `TestMCPToolInvocationRequest`, `TestMCPToolInvocationResponse`, `TestMCPErrorClassification`, `TestIntegration`) are unrelated but `test_basic_instantiation` / `test_full_instantiation` for `MCPToolInvocationRequest` assert default `user_id`/etc. and do not reference `user_context`.
- **`tests/codemie/service/mcp/test_models_1_2_auth_config.py`** — `TestMCPExecutionContextAuthHeaders` (NOT named in the brief):
  - `test_to_request_fields_contains_expected_keys_when_auth_headers_set` (line 205-224) — asserts `fields == {exact 5-key dict}` then `assert "user_context" not in fields` (line 224).
  - `test_to_request_fields_without_auth_headers_matches_previous_contract` (line 226-245) — exact-dict equality + `"user_context" not in fields` (line 245).
  - Other tests here assert `auth_headers` exclusion and `auth_config` round-trips — unaffected.
- **`tests/codemie/service/mcp/test_client.py`** — `TestMCPConnectClientListTools` / invoke tests. Several use `post.assert_called_once_with(json={exact dict}, ...)`. Key cases: `test_list_tools_success` (line 165, no context), `test_list_tools_with_auth` (line 304), `test_list_tools_merges_request_scoped_auth_headers...` (line 363, context has `user_id` + `auth_headers`, no `user_context`), `test_list_tools_keeps_static_headers...` (line 415), `test_invoke_tool_with_execution_context` (line 462, context has `user_id`/`assistant_id`/`project_name`/`workflow_execution_id`, no `user_context`), `test_invoke_tool_merges_request_scoped_auth_headers...` (line 513), `test_invoke_tool_keeps_static_headers...` (line 567). None of these set `user_context` on the context, and the assertion runs against `post_body` (after `model_dump(exclude_none=True)`), so a `user_context=None` is dropped — these are expected to stay green, but must be eyeballed by the plan.
- **`tests/codemie/rest_api/security/test_user_context.py`** — `TestUserContext`: `test_from_user_maps_all_fields`, `test_extra_attributes_absent_from_dump_when_none`, `test_extra_attributes_propagates_through_from_user`, `test_sensitive_fields_excluded_from_model_dump` (asserts `auth_token`/`tenant_id`/`project_limit` absent), `test_sensitive_token_absent_from_model_dump_json`, `test_all_defaults_user_produces_valid_context`, `test_roundtrip_equality`. Good home for new `masked_for_logging()` and "auth_token never in UserContext" coverage if the helper lives near `UserContext`.
- **`tests/codemie/service/provider/test_provider_tool_factory.py`** — `test_built_execute_method` (line 108) already asserts `invocation_request.user_context == UserContext.from_user(fixture_user).model_dump(exclude_none=True)`, `["id"] == "tool_user"`, and `"auth_token" not in invocation_request.user_context`, `"tenant_id" not in ...`. `test_invoke_returns_full_response_and_builds_request` (line 264) asserts request fields but not `user_context`. This is the DSP mirror the MCP tests should match.
- **`tests/codemie/service/mcp/test_execution_context_integration.py`** — exists (per blast radius), instantiates `MCPExecutionContext`; not read this pass — plan should scan for `user_context` / exact-payload assertions.

### Testing Framework and Patterns

- pytest, with `pytest-asyncio` (`@pytest.mark.asyncio`) for client tests; `unittest.mock` (`MagicMock`, `AsyncMock`, `patch`). No custom framework.
- Fixture style: local `@pytest.fixture` per file (`server_config`, `tool_config`, `tool_params`); `httpx.AsyncClient` patched at `codemie.service.mcp.client.httpx.AsyncClient`.
- Model tests are plain constructor + `to_request_fields()` / `model_dump()` assertions, frequently exact-dict equality.
- Seam-test guidance applies: `to_request_fields()` is a policy helper; each callsite (`_build_request_payload` for `tools/list` and `tools/call`) needs a branch test observing the emitted `post_body`.

### Coverage Gaps

- No test asserts `user_context` reaches the MCP wire (`post_body`) when it is set — the opposite is currently locked in.
- No test exists for `masked_for_logging()` / `_mask_email()` / `_mask_value()` — the code does not exist yet.
- No test asserts the MCP invocation log line masks PII (there is no such log line yet).
- No test asserts the DSP invocation log line masks PII.
- `test_execution_context_integration.py` not inventoried for `user_context` assertions this pass.
- End-to-end "user_context reaches a downstream MCP tool" is a QA concern (brief section 9), not covered by unit tests.

---

## 5. Configuration and Environment

### Environment Variables

- No env var gates `user_context` propagation. Brief section 2 is explicit: no feature flag, deploy ordering is the control.
- MCP timeouts adjacent to this path (from `mcp-integration.md`): `MCP_CLIENT_TIMEOUT` (per-invocation `tools/call`, default 300.0s, used at `client.py:83`), `MCP_SERVER_INIT_TIMEOUT`, `MCP_CONNECT_INIT_TIMEOUT`. `BROKER_AUTH_LOCATION_URL` used on 401 handling. None require changes.
- `ENABLE_USER_MANAGEMENT`, `ENV`, `ADMIN_USER_ID`, `ADMIN_ROLE_NAME` affect how `User.is_admin` is resolved (`user.py:58-76`) and therefore what lands in `UserContext` — behavioural context only.

### Configuration Files

- `src/codemie/clients/provider/epam_ai_run.spi.schema.json` (`user_context` block around line 80) — the published DSP contract. Brief section 5 (SPI schema drift: `extra_attributes` / `is_auditor` undeclared) is **out of scope** per the task. MCP-Connect treats `user_context` as an opaque dict, so no schema file governs the MCP wire.
- `config/mcp/mcp-commands-config.yaml` via `MCPCommandsConfig` — unrelated (command allowlisting).
- No feature-flag/toggle file involved.

### Feature Flags and Deployment Concerns

- Hard sequencing constraint (brief section 6): MCP-Connect release that accepts `user_context` must be fully rolled out across all bridge buckets **before** this backend change merges/deploys. Bridge is a StatefulSet; backend routes across buckets (`client.py:85-98`). Ship as two separate releases.
- No secrets handling changes. The point of masked logging is precisely that `user_context` carries PII (`email`, `name`, `picture`) that must not reach log sinks unmasked; MCP-Connect masks its own logs in a separate process.

---

## 6. Risk Indicators

- **Undercounted test surface.** The brief enumerates 5 assertion sites + 2 dedicated tests in `test_models.py`. Research found **two additional tests in a file the brief does not mention** — `tests/codemie/service/mcp/test_models_1_2_auth_config.py::TestMCPExecutionContextAuthHeaders::test_to_request_fields_contains_expected_keys_when_auth_headers_set` (line 224) and `::test_to_request_fields_without_auth_headers_matches_previous_contract` (line 245), each with `assert "user_context" not in fields` plus exact-dict equality. A plan that trusts the brief's list verbatim will leave these red.
- **`to_request_fields()` has no `exclude_none`.** Removing `"user_context"` from the exclude set makes `to_request_fields()` emit `"user_context": None` whenever the context has no user. Every exact-dict-equality assertion on `to_request_fields()` output (`test_models.py` lines ~79-86 / ~99-106 / ~132-139; `test_models_1_2_auth_config.py` lines ~217-223 / ~238-244) will fail on the extra key and must each gain `"user_context": None` (or the tests must switch to subset assertions). This is a broader edit than "flip 5 asserts".
- **`client.py:128` is what keeps `test_client.py` green.** The `exclude_none=True` at the serialization boundary drops the `None` `user_context` before `post.assert_called_once_with(json=...)` sees it. If any future edit moves serialization or a plan "helpfully" adds `exclude_none` to `to_request_fields()` instead, behaviour and the test outcome both shift. The plan must treat `client.py:128` as load-bearing and verify (not assume) the `test_client.py` exact-payload tests still pass.
- **No existing masking utility, no convention.** `masked_for_logging()` / `_mask_value()` / `_mask_email()` must be written from scratch (~40 lines). Placement (mcp module vs security module vs `configs/logger.py`), and the treatment of fields the brief does not name (`knowledge_bases`, `extra_attributes`), are unspecified — an ambiguity the spec must resolve. `extra_attributes` is an unbounded IdP-populated dict flowing to logs; leaving it unmasked in the log helper would be a fresh PII leak even though the brief's keep-readable list omits it.
- **DSP "logs it unmasked" is imprecise.** `provider_tool_factory.invoke()` has no line that prints `user_context` today; the masking requirement there is effectively "add a masked diagnostic log", not "wrap an existing one". Where exactly the DSP masked log line goes (in `invoke()` around line 175-182, or at the `ProviderAPIClient` boundary) is a design decision, not a discovered call site.
- **Two log sites, two modules, two import styles.** MCP uses `codemie.configs.logger.logger`; DSP uses `codemie.configs.logger`. A shared helper must not create an import cycle (e.g. a helper in `rest_api/security/user.py` imported by a `service/` module is fine directionally; the reverse would not be).
- **Deploy-ordering coupling to another repo.** Correctness depends on `codemie-mcp-connect-service` EPMCDME-14847 being deployed first. This backend MR is safe to merge only after that rollout completes; a premature deploy re-breaks all MCP tool invocation (the exact regression that reopened the ticket).
- **Docstrings are part of the deliverable.** Both the field description (`models.py:156-163`) and the method docstring (`models.py:165-177`) assert the EPMCDME-13546 rationale as current fact. Leaving either stale ships a contradiction into the code the next reader will trust.
- **Speculative (design, not research):** the new masking helper will likely need its own test module (e.g. `tests/codemie/.../test_user_context.py` extension or a new `test_log_masking.py`); the tool-owner doc will need a new path under `docs/` since no MCP/tool-owner subtree exists. Neither is an existing constraint — the spec/plan decides.
- **Speculative:** `test_execution_context_integration.py` may contain further `user_context`/payload assertions not inventoried this pass; the plan should grep it before estimating the test delta.

---

## 7. Summary for Complexity Assessment

The production behaviour change is genuinely small and well-scoped: delete one entry (`"user_context"`) from the `MCPExecutionContext.to_request_fields()` exclude set in `src/codemie/service/mcp/models.py` and rewrite two co-located docstrings that still cite EPMCDME-13546. Every supporting piece already exists — the field is declared on both `MCPExecutionContext` (line 156) and the outbound `MCPToolInvocationRequest` (line 504), it is populated in `toolkit_service.py:243`, the single request-construction/serialization site is `client.py:100-128`, and `exclude_none=True` at `client.py:128` already drops the key when there is no user. The DSP path (`provider_tool_factory.py:175`) is a working, tested mirror of the target behaviour. Layers touched: MCP wire models, the MCP service/client layer, the DSP service layer (for the log line only), a new shared logging helper, the test suite, and developer docs. The `UserContext` model and SPI schema are explicitly out of scope.

Complexity is concentrated in three softer areas rather than the code diff. First, the test surface is larger and more brittle than the brief states: beyond the 5 asserts + 2 dedicated tests in `test_models.py`, there are two more locking tests in `test_models_1_2_auth_config.py`, and because `to_request_fields()` carries no `exclude_none`, roughly five exact-dict-equality assertions across those two files will each need `"user_context": None` added — plus positive tests for the new behaviour and for a helper that does not yet exist. Second, `masked_for_logging()` / `_mask_value()` / `_mask_email()` must be written from scratch with no precedent in the codebase and no guide; the mask spec leaves `knowledge_bases` and `extra_attributes` (an unbounded IdP dict) untreated, which the spec must pin down. Third, the tool-owner documentation is net-new with no home in `docs/`.

The dominant risk is not technical difficulty but coordination and thoroughness: the change is only safe to deploy after the paired MCP-Connect release (EPMCDME-14847) has fully rolled out across all bridge buckets, and a plan that copies the brief's test-site list verbatim will ship with a red suite. Estimate the work as small-to-moderate: a handful of production lines, ~40 lines of helper, ~8-12 test edits/additions across 3-4 files, one documentation file, and a deploy-sequencing note in the MR.

---

## 8. External References

- **`EPMCDME-13260-backend-task.md`** (repo root — resolved: `C:\Users\Yauheni_Hil\repos\codemie-dev\codemie\EPMCDME-13260-backend-task.md`). The authoritative brief; read in full. Facts the task needs from it:
  - **Contract**: backend sends `user_context` as a plain field on `MCPToolInvocationRequest` (already declared, `models.py:504`); MCP-Connect accepts it and forwards it as MCP `_meta["codemie.ai/user-context"]` on `tools/call` only. Backend never touches `_meta`. `tools/list` carries it harmlessly. No new field/model/schema, no feature flag.
  - **Code change (section 3)**: delete `"user_context",` from the exclude set at `models.py:185`; rewrite the method docstring (`models.py:165-177`, brief says 173-176) and the field description (`models.py:156-163`) so they state `user_context` IS serialised and forwarded (EPMCDME-13260), requiring an MCP-Connect release that accepts the field. That is the entire production-code diff.
  - **Masking (section 4)**: re-implement `masked_for_logging()` / `_mask_value()` / `_mask_email()` (~40 lines) on current `main` — do NOT cherry-pick from `origin/EPMCDME-13260` (241 commits behind; its MCP log guard `if request.user_context:` is always falsy). Mask `email` (`j***@epam.com`), `name`, `picture`, `username` (partial). Keep readable: `id`, `roles`, `is_admin`, `is_maintainer`, `is_auditor`, `user_type`, `project_names`, `admin_project_names`. Apply at `src/codemie/service/mcp/client.py` (MCP invocation) and `src/codemie/service/provider/provider_tool_factory.py` (DSP). Required by EPMCDME-13546 AC. Both services mask independently (separate processes/sinks).
  - **Tests (section 7)**: `tests/codemie/service/mcp/test_models.py` asserts the field is never sent at lines 87, 107, 140, 190, 207. Delete the two dedicated tests at `:177` (`test_to_request_fields_excludes_user_context_regardless_of_value`) and `:197` (`test_to_request_fields_never_includes_user_context`) rather than flipping them. Add positive coverage: `user_context` present when set; absent when `None` via `exclude_none=True` at `client.py:128`; `auth_token` never in `UserContext`; `masked_for_logging()` masks `email`/`name`/`picture`, keeps `roles`/`is_admin`/`project_names`.
  - **Documentation (section 8)**: none exists in `docs/`; ticket cannot close without it. Must cover: the `_meta` key `codemie.ai/user-context`; the field list, all optional; values backend-populated from the authenticated session, tools cannot inject/override; trust model transport-level (only the CodeMie backend can reach the tool), NOT signed; no credentials (`auth_token` excluded — to act as the user use `mcp_headers` / `env`); PII obligations for tool owners handling `email`/`name`/`picture`; it is a per-call snapshot, not a session.
  - **Sequencing (section 6)**: MCP-Connect MR merges, deploys, and completes rollout across all buckets FIRST; then this MR; then QA re-verify. Bridge is a StatefulSet; backend routes across buckets (`client.py:85-98`). Un-excluding before the bridge accepts the field recreates EPMCDME-13546 in production.
  - **QA (section 9)**: regression check `tests/assistant/tools/mcp/test_cli_mcp_server.py::test_cli_mcp_server[ls]`; verify `user_context` reaches a downstream MCP tool end-to-end; verify DSP path does not regress; verify masked PII in both services' logs.
  - **Explicitly OUT OF SCOPE per task_context** (overrides brief section 5 and its section-10 checkboxes): do NOT drop `extra_attributes` from `UserContext.from_user()` (`user.py:165`); do NOT add `is_auditor` to `epam_ai_run.spi.schema.json`. MCP mirrors current DSP behaviour and forwards `user_context` including `extra_attributes` exactly as DSP does today.
  - **Reference table (section 11)** confirms file:line anchors, all read at commit `0b260a14c` (current HEAD): `UserContext` `user.py:127`; `from_user` `user.py:150`; exclusion to delete `models.py:185`; `MCPExecutionContext.user_context` `models.py:156`; `MCPToolInvocationRequest.user_context` `models.py:504`; population `toolkit_service.py:243`; request construction `client.py:123`; serialization `client.py:128`; bucket routing `client.py:85-98`; DSP propagation `provider_tool_factory.py:175`; DSP wire model `tool_invocation_request.py:43`; SPI schema `epam_ai_run.spi.schema.json:80`; leave-alone env injection `toolkit_service.py:1696`.
- No other path or URL was named by the task as a source of truth (the paired `codemie-mcp-connect-service/EPMCDME-13260-mcp-connect-task.md` is referenced by the brief but lives in a different repository and is not part of this backend task's scope).
