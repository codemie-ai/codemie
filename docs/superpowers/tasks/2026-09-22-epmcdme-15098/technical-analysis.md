# Technical Research

**Task**: mcp access control governance validation
**Generated**: 2026-09-22
**Research path**: filesystem

---

## 1. Original Context

**Summary:** Enforce MCP Policy on All Persisting Save Paths

**Story:**
As a platform administrator, I want every save that persists an MCP server to be checked against the platform toggle, so that an unapproved server cannot be stored at all, whichever client or script performed the save.

**Background:**
One shared service-layer validator, called from all seven persisting paths, so no entry path can accept what another rejects.

When the toggle is on it refuses three things: a hand-written MCP server, a catalogue-referenced server carrying its own connection configuration, and a catalogue-referenced server that declares custom mode. The third matters because declaring custom mode is itself an override.

Read the existing implementation before estimating: much of this is already built, and the remaining gap is narrow.

`MCPAccessControlService._validate_restricted_mode` already raises when a server has no `mcp_config_id`, so hand-written servers are already refused at save time while the toggle is on. `_CATALOG_REF_ALLOWED_FIELDS` already excludes config, command, arguments and `mcp_connect_url`, so a catalogue-referenced server carrying inline connection configuration is already refused too.

The actual hole is narrower: `use_custom_config` and tools ARE in `_CATALOG_REF_ALLOWED_FIELDS`, so a user can still declare custom mode on a catalogue-referenced server under today's restricted mode. Removing `use_custom_config` from that set is the substantive change.

The rest of this sub-task is proof rather than new enforcement. Of the nine enforcement call sites — assistant create, assistant update and the inline/virtual-run endpoint in `routers/assistant.py`; create and update in `routers/workflow.py`; create and update in `skill_service.py`; two in `external/deployment_scripts/preconfigured_assistants.py` — only the two deployment-template ones have a test proving the validator is actually invoked. The guard could be deleted from any router path and the suite would stay green. The criteria below are therefore written to be regression guards for behaviour that partly exists already, not only specifications of new behaviour.

Two known test traps: `ENV=local` forces admin privileges under pytest, and the existing mode fixtures return one constant for every setting lookup.

**Acceptance Criteria:**
1. Given the toggle is on, when I save an assistant, skill or workflow containing a hand-written MCP server, then the save is rejected and the message names the offending server and states that platform policy caused it.
2. Given the toggle is on, when I save a catalogue-referenced server that carries its own connection configuration, then the save is rejected with a comparable message.
3. Given the toggle is on, when I save a catalogue-referenced server that declares custom mode without carrying configuration, then the save is rejected — declaring custom mode is itself an override.
4. Given the toggle is on, when I save a catalogue-referenced server supplying only the values its catalogue entry declares as user-supplied, such as my own credentials, then the save succeeds.
5. Given the toggle is on, when the same violating payload is submitted through each persisting save path in turn — assistant create, assistant update, the third assistant save path, skill create, skill update, workflow create and workflow update — then every path rejects it and none accepts what another rejects.
6. Given a save path is expected to enforce the policy, when its guard is removed, then at least one test fails.
7. Given I am an admin or maintainer building my own assistant, when my payload violates the policy, then the save is rejected exactly as it is for any other user — administrative rank is not a bypass.
8. Given the toggle is off, when I save a hand-written server or override a catalogue entry's configuration, then the save succeeds exactly as it does today.

**Out of Scope:** the toggle definition/storage itself (separate sub-task EPMCDME-15096), tool preview/ad-hoc connection test/managed-server catalogue, run-time blocking of already-stored servers, deployment-template validation at startup, any UI.

---

## 2. Codebase Findings

### Existing Implementations

The enforcement mechanism already exists and is already wired into every named call site. This is not greenfield work.

- `src/codemie/service/mcp/access_control.py` — `MCPAccessControlService`, the single enforcement point:
  - `_CATALOG_REF_ALLOWED_FIELDS` (lines 26-39) — frozenset of fields a catalog-ref server (`mcp_config_id` set) may still carry: `name`, `description`, `enabled`, `mcp_config_id`, `use_custom_config`, `tools`, `tools_tokens_size_limit`, `settings`, `integration_alias`, `resolve_dynamic_values_in_arguments`. Everything else (`config`, `command`, `arguments`, `mcp_connect_url`) is already excluded.
  - `_validate_restricted_mode` (lines 56-69) — for every server: raises `ValidationException("Custom MCP servers are not allowed in restricted mode. Server '{server.name}' must reference a catalog entry via mcp_config_id.")` when `mcp_config_id` is falsy; otherwise loops `MCPServerDetails.model_fields` and raises `ValidationException("Field '{field}' is not allowed when mcp_config_id is set in restricted mode (server '{server.name}').")` for any field **not** in the allowed set whose current value **is not `None`**.
  - `validate_on_save` (lines 96-115) — calls `_validate_restricted_mode` only when `customer_config.is_component_enabled("mcpCustomServersDisabled")` is true, then always runs `_validate_catalog_entries` (duplicate `mcp_config_id`, missing/inactive/non-public catalog entry) regardless of toggle state.
  - `sanitize_for_save` (lines 126-131) — thin wrapper: `validate_on_save` then return unchanged list.
  - `strip_inline_config`/`_strip_one` (lines 117-124) — documented no-ops; inline overrides are preserved, not stripped.
  - `filter_for_runtime`/`resolve_catalog_config` — runtime read-path helpers, out of this ticket's scope (governs already-stored data, not save-time rejection).
- `src/codemie/rest_api/models/assistant.py:174-215` — `MCPServerDetails` model. Relevant field defaults: `mcp_config_id: Optional[str] = None`, `config: Optional[MCPServerConfig] = None`, `use_custom_config: bool = False` (default is `False`, **not** `None`), `mcp_connect_url/command/arguments: Optional[str] = None`, `settings: Optional[SettingsBase] = None` (line 228, "Must be renamed to environment_vars"), `integration_alias: Optional[str] = None`, `resolve_dynamic_values_in_arguments: bool = False`.

**The nine call sites — confirmed present and calling the validator today:**
1. `src/codemie/rest_api/routers/assistant.py:743` — `create_assistant`: `request.mcp_servers = MCPAccessControlService.sanitize_for_save(request.mcp_servers)`.
2. `src/codemie/rest_api/routers/assistant.py:862` — `update_assistant`: same call, before `repository.update(...)`.
3. `src/codemie/rest_api/routers/assistant.py:977` — `ask_virtual_assistant` (`/v1/assistants/virtual/model`): `mcp_servers = MCPAccessControlService.sanitize_for_save(request.mcp_servers)`. Not persisted (`Assistant.model_construct(...)`, no `.save()`), but validated exactly like the persisting paths — this is the "third assistant save path" / "inline/virtual-run endpoint" named in the ticket and AC5.
4. `src/codemie/rest_api/routers/workflow.py:396` — `create_workflow`: `MCPAccessControlService.validate_on_save(_collect_workflow_mcp_servers(workflow_config))` inside the `try` block, before `_strip_workflow_mcp_servers` and `workflow_service.create_workflow(...)`. `_collect_workflow_mcp_servers` (lines 81-89) walks both `workflow_config.assistants[].mcp_servers` and `workflow_config.tools[].mcp_server`.
5. `src/codemie/rest_api/routers/workflow.py:474` — `update_workflow`: same collect+validate pattern on `updated_config`.
6. `src/codemie/service/skill_service.py:506` — skill create: `skill_data["mcp_servers"] = MCPAccessControlService.sanitize_for_save(skill_data.get("mcp_servers"))`, before `SkillRepository.create(skill_data)`.
7. `src/codemie/service/skill_service.py:649` — skill update: `updates["mcp_servers"] = MCPAccessControlService.sanitize_for_save(updates["mcp_servers"])`, guarded by `if "mcp_servers" in updates:`, before `SkillRepository.update(...)`.
8. `src/external/deployment_scripts/preconfigured_assistants.py:148` — `update_assistant_content`: `validated_mcp_servers = MCPAccessControlService.sanitize_for_save(assistant_template.mcp_servers)`, only entered `if assistant_template.mcp_servers:` and after `_validate_mcp_server_names()`.
9. `src/external/deployment_scripts/preconfigured_assistants.py:224` — `create_preconfigured_assistant`: same pattern, guarded by `if error := assistant_template.validate_fields():` first.

All nine sites resolve to the same `MCPAccessControlService.validate_on_save`/`sanitize_for_save` code path in `access_control.py`; there is exactly one shared validator as the ticket requires.

### The narrow gap, traced precisely

`use_custom_config` and `tools` are in `_CATALOG_REF_ALLOWED_FIELDS` today, so a catalog-ref server (`mcp_config_id` set) with `use_custom_config=True` and no other forbidden field passes `_validate_restricted_mode` unchanged — this is the hole AC3 targets.

Removing `use_custom_config` from the frozenset is **not** a safe drop-in fix by itself: the per-field check at line 65 is `getattr(server, field, None) is not None`, and `use_custom_config`'s default is `False` (a bool), not `None`. Every catalog-ref server carries `use_custom_config=False` by default (the non-override, correct case), and `False is not None` evaluates `True`. A bare removal of `use_custom_config` from the set would therefore reject **every** catalog-ref server, including the ones AC4 requires to pass, not just the ones declaring custom mode — this would also break the existing passing test `test_valid_catalog_ref_passes_in_restricted_mode` in `tests/unit/service/mcp/test_access_control.py:191-196`, which builds its server via `_server()` whose `use_custom_config` default is `False`. Enforcing AC3 correctly needs a check keyed on the field's *truthy* value (`server.use_custom_config is True`), not on the generic "value is not None" frozenset-membership test that the rest of `_validate_restricted_mode` uses. `enabled` (default `True`) already sits in the allowed set for the same class of reason — the frozenset's implicit invariant is "every field outside it must default to `None`," and `use_custom_config` violates that invariant once removed.

### Architecture and Layers Affected

- **Service layer** — `MCPAccessControlService` (`src/codemie/service/mcp/access_control.py`) is the sole enforcement point; `SkillService` (`src/codemie/service/skill_service.py`) calls it directly (no router layer for skills — `skill_service.py` is called straight from wherever skill endpoints live).
- **REST API / router layer** — `src/codemie/rest_api/routers/assistant.py` (`create_assistant`, `update_assistant`, `ask_virtual_assistant`), `src/codemie/rest_api/routers/workflow.py` (`create_workflow`, `update_workflow`, plus helpers `_collect_workflow_mcp_servers`, `_strip_workflow_mcp_servers`).
- **Deployment/bootstrap layer** — `src/external/deployment_scripts/preconfigured_assistants.py` (`create_preconfigured_assistant`, `update_assistant_content`), a startup script from a prior ticket (EPMCDME-8996), not a router.
- **Config/toggle layer** — `src/codemie/configs/customer_config.py` singleton `customer_config`, read via `customer_config.is_component_enabled("mcpCustomServersDisabled")` — imported separately into `access_control.py` (`codemie.service.mcp.access_control.customer_config`) and into `routers/workflow.py` (`codemie.rest_api.routers.workflow.customer_config`, used there for an unrelated `subWorkflow` feature check at line ~150) — **two distinct import bindings of the same underlying singleton**, relevant to how tests must patch it (see Section 4).
- **Model layer** — `MCPServerDetails` (`src/codemie/rest_api/models/assistant.py:174`), `MCPConfig` (`src/codemie/rest_api/models/mcp_config.py`, catalog entries).

### Integration Points

- `access_control.py` depends on `codemie.configs.customer_config.customer_config` (module singleton) and `MCPConfig.get_by_ids`/`MCPConfig.find_by_id` for catalog lookups.
- `routers/workflow.py`'s `_collect_workflow_mcp_servers` reaches into both `WorkflowConfig.assistants[].mcp_servers` and `WorkflowConfig.tools[].mcp_server` — a workflow can carry MCP servers in two different shapes, both validated by one `validate_on_save` call per create/update.
- No router or service call site passes the current `User` into `validate_on_save`/`sanitize_for_save` — the validator takes only a server list. There is no code path anywhere in `assistant.py`, `workflow.py`, or `skill_service.py` that conditions the `MCPAccessControlService` call on `user.is_admin`/`is_maintainer` (confirmed by grep: no `is_admin`/`is_maintainer` reference near any of the nine call sites). AC7 ("administrative rank is not a bypass") holds structurally today because no bypass branch exists to remove — it would need to be an explicit new bypass to break it.

### Patterns and Conventions

- **Single shared validator, called, not duplicated**: every save path calls the same static method; none re-implements a parallel check.
- **Allowed-fields frozenset pattern**: `_CATALOG_REF_ALLOWED_FIELDS` is a manually maintained allow-list; the per-field loop assumes every field outside it defaults to `None` (true for all current members except `use_custom_config`, once removed).
- **ValidationException naming convention**: existing messages both name the server (`server.name`) and state the mechanical cause (`"...in restricted mode"` / `"...not allowed when mcp_config_id is set..."`). AC1/AC2 ask for a message that "names the offending server and states that platform policy caused it" — the existing two message templates already do this; a new AC3 message should follow the same two-part shape.
- **Deployment-script call sites are guarded by prior validation**: both `preconfigured_assistants.py` sites only call `sanitize_for_save` after `_validate_mcp_server_names()`/`validate_fields()` already passed, i.e. `MCPAccessControlService` validation is the second gate, not the first, in that file.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` — MCP connection/timeout guidance; does not mention catalogue governance or `mcpCustomServersDisabled`.
- No `.ai-run/guides/` entry covers `MCPAccessControlService` or the restricted-mode validator specifically; behavior is documented only in code and in prior sub-task research docs (below).

### Architectural Decisions

- `docs/superpowers/tasks/2026-09-18-epmcdme-15094-mcp-catalogue-governance/technical-analysis.md` — parent-epic research (this ticket's direct predecessor context). Confirms the same nine/seven call-site inventory, the `_CATALOG_REF_ALLOWED_FIELDS` pattern, and that `strip_inline_config`/`_strip_one` are deliberate no-ops (inline overrides win at runtime; the actual restriction happens earlier, in `_validate_restricted_mode`).
- `docs/superpowers/tasks/2026-09-21-epmcdme-15096-mcp-governance-switches/technical-analysis.md` and `spec.md` — sibling sub-task (out of scope here per the ticket) that registers `mcpCustomServersDisabled` as a runtime-toggleable `customer_config_declarations.DECLARATIONS` entry. States explicitly: "Enforcement (`MCPAccessControlService`, epic sub-task A2) already reads this flag via `customer_config.is_component_enabled("mcpCustomServersDisabled")` and is already implemented/tested" and "Registering the declaration therefore makes `MCPAccessControlService`'s own enforcement check runtime-toggleable for free, with zero changes to `access_control.py`" — i.e. this sub-task's validator code does not need to change for the toggle to become admin-editable; that is EPMCDME-15096's concern, already researched/speced separately.
- `docs/superpowers/tasks/2026-07-08-preconfigured-assistant-mcp-improvements/spec.md` (EPMCDME-8996) — prior ticket that originally wired `sanitize_for_save` into the two deployment-script call sites; documents the "fail-fast in restricted mode" decision for that script (a restricted-mode deployment with an inline-config template raises `ValidationException` and startup fails).
- No trace found anywhere in the repo of a `confirm_custom_override`/second-toggle mechanism (EPMCDME-13493, named as reverted precedent in the parent epic's ticket) — confirms no override-confirmation code exists to interact with.

### Derived Conventions

- `ValidationException` messages in `access_control.py` name the server and the offending field/cause explicitly — any new AC3 message should keep exactly this two-part shape (server name + platform-policy cause) rather than introducing a new format.
- Router-level MCP validation always happens in a `try`/`except` block that maps `ValidationException` (or any exception, in `workflow.py`'s case) to an HTTP 400 with `details` derived from the exception message — so the exact `ValidationException` text becomes the client-visible detail.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/unit/service/mcp/test_access_control.py` — thorough unit coverage of `MCPAccessControlService` itself: open/restricted mode for `validate_on_save`, every currently-forbidden field (`config`, `command`, `arguments`, `mcp_connect_url`), catalog-entry validity checks, `strip_inline_config`, `filter_for_runtime`, `resolve_catalog_config`. **No test exercises `use_custom_config` as a restricted-mode violation** — confirmed by reading the full file; every restricted-mode test in `TestValidateOnSaveRestrictedMode` uses one of the four already-forbidden fields, never `use_custom_config=True`. This is the exact coverage gap AC3 must close.
- `tests/external/deployment_scripts/test_preconfigured_assistants.py` — the **only** test file anywhere that patches `MCPAccessControlService.sanitize_for_save` at an actual call site and asserts it was called (`mock_sanitize.assert_called_once_with([_MCP_SERVER_A])`, lines 608, 642, 679) and that a `ValidationException` from it propagates and blocks the save (`test_create_new_restricted_mode_inline_servers_raises` line 586, `test_update_restricted_mode_inline_servers_raises` line 664). This matches the ticket's claim precisely: only these two call sites have "a test proving the validator is actually invoked."
- No other test file references `sanitize_for_save`/`validate_on_save` at all — confirmed by a repo-wide grep for those two names across `tests/`, which returns only `test_access_control.py` and `test_preconfigured_assistants.py`.
- `tests/codemie/rest_api/routers/test_assistant.py` — exists and is substantial (guardrails, ask_assistant, slug handling, version/skill_ids), but **no test in it references `mcp_servers`, `MCPServerDetails`, or `MCPAccessControlService`** — confirmed by grep. `create_assistant`/`update_assistant`'s MCP guard (call sites 1 and 2) has zero test coverage today.
- `tests/codemie/rest_api/routers/test_workflow.py` — exists, but a grep for `mcp_servers`/`mcp_server`/`MCPServerDetails` returns **no matches at all**. `create_workflow`/`update_workflow`'s MCP guard (call sites 4 and 5) has zero test coverage today.
- `tests/codemie/service/test_skill_service.py` — has `mcp_servers` references (lines ~1781-1851) but only for `_build_skill_updates` dict-construction logic; none of them patch or assert `MCPAccessControlService.sanitize_for_save`. Call sites 6 and 7 have zero test coverage of the governance guard specifically.
- `tests/codemie/rest_api/routers/test_assistant_mcp_tools.py` — exists and touches MCP, but has zero references to `customer_config`, `is_component_enabled`, or "restricted" — it covers MCP tool listing/execution, not save-time governance.

### Testing Framework and Patterns

- `pytest` 8.3.3 (confirmed installed and runnable directly), `unittest.mock.patch`/`MagicMock`, module-path patch targets (e.g. `_CC = "codemie.service.mcp.access_control.customer_config"` in `test_access_control.py`).
- FastAPI router tests use `httpx.AsyncClient`/`ASGITransport` against the real `app` object with `app.dependency_overrides[authenticate] = lambda: user` (see `test_assistant.py`, `test_workflow_selectable.py`) rather than mocking the router function directly.
- **Test trap 1 — `ENV=local` forces admin.** `pytest.ini` sets `env = ENV=local` (line 6) for the whole suite. `User.resolve_is_admin` (`src/codemie/rest_api/security/user.py:58-76`) has: `if Environment.LOCAL.value == config.ENV: self.is_admin = True` — unconditionally, before any role-based logic. Under pytest, **every** `User(...)` instance constructed gets `is_admin=True` regardless of what `is_admin`/`is_maintainer`/`roles` are passed at construction, because the `model_validator(mode='after')` overwrites it. A test written to prove AC7 ("admin is not a bypass") by constructing a non-admin `User` and asserting rejection will silently test an admin user instead, unless it explicitly monkeypatches `config.ENV` away from `"local"` or patches `resolve_is_admin`/`is_admin_or_maintainer` directly for that test.
- **Test trap 2 — mode fixtures return one constant for every setting lookup.** The established pattern across this codebase for gating tests, e.g. `tests/codemie/rest_api/routers/test_workflow_selectable.py:41-44`: `mock_cc = MagicMock(); mock_cc.is_feature_enabled.return_value = enabled; patch("codemie.rest_api.routers.workflow.customer_config", mock_cc)`. This mock's `return_value` is not keyed by the component-id argument at all — `is_feature_enabled("anyKey")` and `is_feature_enabled("otherKey")` both return the same `enabled` constant. The same shape recurs throughout `tests/codemie/workflows/nodes/test_sub_workflow_node.py`, `test_customer_config.py`, `test_cost_center_service.py`, etc. `tests/unit/service/mcp/test_access_control.py`'s own `_open_mode()`/`_restricted_mode()` helpers (lines 34-43) use the identical shape: `m.is_component_enabled.return_value = False/True` with no `side_effect` keyed by argument. Two concrete consequences for writing this sub-task's tests: (1) a single mocked `customer_config` cannot represent "mcpCustomServersDisabled is on but some other flag is off" in the same call — any test needing both must use `side_effect=lambda key: {...}.get(key)` instead of `return_value`; (2) `access_control.py` and `routers/workflow.py` import `customer_config` as two separate module-level names, so a router-level test that patches `codemie.rest_api.routers.workflow.customer_config` (as the existing `_patch_sub_workflow_enabled` helper does) has **no effect** on `MCPAccessControlService.validate_on_save`, which reads `codemie.service.mcp.access_control.customer_config` — a new router-level restricted-mode test must patch that second path specifically or it will silently exercise open-mode behavior regardless of what it intends to assert.

### Coverage Gaps

- No test for `use_custom_config=True` on a catalog-ref server in restricted mode (AC3) — the field is absent from every existing forbidden-field test.
- No test proving `MCPAccessControlService` is invoked at `assistant.py` create/update/virtual (call sites 1-3), `workflow.py` create/update (call sites 4-5), or `skill_service.py` create/update (call sites 6-7) — AC5 and AC6 require exactly this proof, and it does not exist for seven of the nine sites today.
- No test constructs a non-admin `User` under the `ENV=local` pytest environment and confirms rejection is unaffected by admin status (AC7) — no existing test touches admin/non-admin distinction for MCP governance at all.
- No test toggling `mcpCustomServersDisabled` off and asserting today's pass-through behavior for a hand-written or overridden catalog-ref server at any of the seven router/service call sites (AC8) — `test_access_control.py`'s open-mode tests cover the service directly, not any call site.

---

## 5. Configuration and Environment

### Environment Variables

- `ENV` — read via `config.ENV`; set to `local` for the entire pytest run via `pytest.ini`'s `env = ENV=local` block. Drives `User.resolve_is_admin`'s dev-admin override (see Section 4 trap 1). Also referenced in `src/codemie/rest_api/security/user_providers/persistent.py:105` and `src/codemie/rest_api/security/idp/local.py:84` for other dev-only auth shortcuts.
- No `MCP_CUSTOM_SERVERS_DISABLED`-style environment variable exists; the toggle is a plain `customer_config` component id, not `features:`-prefixed, so it has no env-var override path (per the sibling sub-task's research, not re-verified independently here since it is out of this ticket's scope).

### Configuration Files

- `pytest.ini` — `env = ENV=local`, `REPOS_LOCAL_DIR=./codemie-repos`, `PG_URL=...`; `addopts = --import-mode=importlib -n 2` (parallel test execution via `pytest-xdist`, relevant if new tests share module-level mutable state).
- `config/customer/customer-config.yaml` — defines the `mcpCustomServersDisabled` component (per parent-epic research; not re-read in full here as it is EPMCDME-15096's concern, out of scope for this sub-task).

### Feature Flags and Deployment Concerns

- The toggle is read via `customer_config.is_component_enabled("mcpCustomServersDisabled")` — a single boolean gate with no per-project or per-user override in `access_control.py` itself (confirmed: no `project`/`user` parameter anywhere in `MCPAccessControlService`).
- `src/external/deployment_scripts/preconfigured_assistants.py`'s two call sites run at deployment/startup time (per prior ticket EPMCDME-8996's "fail-fast" decision), not inside a request — a `ValidationException` there aborts template application rather than returning an HTTP 400.

---

## 6. Risk Indicators

- **The stated "substantive change" (drop `use_custom_config` from `_CATALOG_REF_ALLOWED_FIELDS`) is not safe as a literal deletion.** The field's default is `False`, not `None`, so the existing generic "not-allowed-and-not-`None`" check would reject every catalog-ref server, including the AC4 pass-through case, not just AC3's violation case. Speculative: the correct fix is almost certainly a dedicated `if server.use_custom_config: raise ...` check alongside (not instead of) the frozenset loop, keeping `use_custom_config` out of, or handling it separately from, the generic membership test — this is a design/plan decision, not something already resolved in the code today.
- **Two distinct `customer_config` import bindings exist** (`codemie.service.mcp.access_control.customer_config` vs `codemie.rest_api.routers.workflow.customer_config`), and the codebase's own established test pattern (`test_workflow_selectable.py`) patches the router-level binding. A new test that reuses that exact helper/pattern for an assistant/workflow/skill router-level MCP test will patch the wrong object and pass regardless of actual enforcement — silently defeating AC6's "guard removal must fail at least one test" requirement.
- **Seven of nine call sites have zero regression coverage today** (assistant create/update/virtual, workflow create/update, skill create/update) — confirmed by grep showing `sanitize_for_save`/`validate_on_save` appear in test code only for the service unit tests and the two deployment-script sites. AC6 is not satisfied for any of these seven today; the guard is provably deletable from any of them without a test failure.
- **`ENV=local` forcing admin under pytest is a real trap for AC7.** Any test built the "obvious" way — construct a `User(is_admin=False, is_maintainer=False, ...)` and expect rejection — will actually run against an admin user under the suite's own `pytest.ini` environment, because `User.resolve_is_admin` unconditionally sets `is_admin=True` when `config.ENV == "local"`. A test for AC7 must explicitly neutralize this (patch `config.ENV`, patch `is_admin_or_maintainer`, or patch the resolved property) or it will pass without ever exercising the non-admin path it claims to cover.
- **Mode/settings mocks in this codebase are conventionally single-`return_value` MagicMocks, not argument-keyed.** Writing a test that needs "`mcpCustomServersDisabled` on, something else off" (or vice versa) in the same assertion requires a `side_effect` callable; copying the existing single-`return_value` convention will silently make unrelated checks agree with the one under test.
- **`_CATALOG_REF_ALLOWED_FIELDS`'s implicit invariant (every excluded field defaults to `None`) is undocumented in code** — a future field addition to `MCPServerDetails` with a non-`None` default (as `use_custom_config` already demonstrates) will hit the same class of bug this ticket is fixing, with no test or comment currently warning against it.
- **`ask_virtual_assistant` (call site 3) is not persisted but is validated** — a naive interpretation of "persisting save paths" could lead someone to (incorrectly) treat call site 3 as out of scope for the regression tests, when AC5 explicitly names it ("the third assistant save path").

---

## 7. Summary for Complexity Assessment

This ticket lands on code where the enforcement mechanism is already built and already wired into all nine named call sites — `MCPAccessControlService.validate_on_save`/`sanitize_for_save` in `src/codemie/service/mcp/access_control.py`, called from `routers/assistant.py` (create, update, virtual), `routers/workflow.py` (create, update), `skill_service.py` (create, update), and `external/deployment_scripts/preconfigured_assistants.py` (create, update). The one confirmed functional gap is narrow but not mechanically trivial: `use_custom_config` sits in `_CATALOG_REF_ALLOWED_FIELDS` today, and removing it naively breaks the pass-through case (AC4) because the field's default is `False`, not `None`, unlike every other field the generic per-field loop excludes — the fix needs a dedicated truthy check for that one field, not a one-line frozenset edit. Everything else — hand-written server rejection, inline-config rejection, catalog-entry validity — is already implemented and already unit-tested in `tests/unit/service/mcp/test_access_control.py`.

The larger share of the work, per the ticket's own framing, is proof rather than new enforcement: seven of the nine call sites (everything except the two deployment-script sites) have zero test coverage proving the validator is actually invoked, confirmed by a repo-wide grep showing `sanitize_for_save`/`validate_on_save` referenced in test code only in the service's own unit tests and `tests/external/deployment_scripts/test_preconfigured_assistants.py`. `test_assistant.py`, `test_workflow.py`, and `test_skill_service.py` all exist and are substantial but contain no `mcp_servers`/`MCPServerDetails`/`MCPAccessControlService` references at all for the create/update paths this ticket must guard. Writing tests that actually distinguish toggle-on from toggle-off, and that actually fail when a guard is removed (AC6), requires navigating two specific traps present throughout this codebase's existing test suite: `pytest.ini`'s `ENV=local` unconditionally forces every constructed `User` to `is_admin=True` regardless of constructor arguments (a real risk for AC7's non-admin-equivalence test), and the codebase's conventional mode-mocking pattern (`MagicMock().is_component_enabled.return_value = X`) is not argument-keyed and, in the router test files, targets a different module-level `customer_config` binding than the one `access_control.py` actually reads — a naively-copied test would silently exercise the wrong code path.

Key risk factors for complexity scoring: the `use_custom_config` default-value trap is a genuine correctness subtlety, not busywork; the near-total absence of call-site-level regression tests across seven paths means this ticket is effectively writing first-time coverage for those paths from scratch; and the two named test traps are both live, reproducible patterns already present elsewhere in this test suite, not hypothetical concerns.

---

## 8. External References

None named by the task. The task's background section references the parent epic (EPMCDME-15094) and prior implementation work as context for "much of this is already built" — this was resolved to `src/codemie/service/mcp/access_control.py` and its existing test suite, both in-repo and covered under Section 2/4 above rather than reported here as an external source. The task also implicitly assumes the reader has access to two sibling-ticket research documents already present in this repo (`docs/superpowers/tasks/2026-09-18-epmcdme-15094-mcp-catalogue-governance/technical-analysis.md` and `docs/superpowers/tasks/2026-09-21-epmcdme-15096-mcp-governance-switches/technical-analysis.md`); both were read in full and their relevant conclusions are cited in Section 3 above.
