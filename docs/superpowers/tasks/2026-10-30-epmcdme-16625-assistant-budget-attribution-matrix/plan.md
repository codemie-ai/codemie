# Assistant Budget Attribution Matrix Implementation Plan

> **For agentic workers:** `sdlc-task` owns execution: inline TDD in the current conversation (no subagent per task). Steps use checkbox (`- [ ]`) syntax.

**Goal:** One resolver charges every LLM request target → D (default project) → P (personal), used by both the web path and the CLI/IDE proxy, with username as the canonical personal id and analytics that show the charged project, the original target and the fallback kind.

**Architecture:** A pure decision module `service/budget/attribution_resolver.py` takes (asset kind, asset project, global flag, user memberships/default, optional CLI project) and an injected `is_funded(project)` callable and returns an `AttributionDecision`. The funded check is collapsed into one shared probe (sync+async) over the existing `_resolution_cache`. `set_llm_context` (web) and `proxy_router` (CLI) both call the resolver; `LiteLLMContext`/`request_info` carry the decision to analytics.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy, pydantic, cachetools TTLCache, pytest + unittest.mock, ruff.

**Spec:** `docs/superpowers/tasks/2026-10-30-epmcdme-16625-assistant-budget-attribution-matrix/spec.md` (ticket text: `ticket.md`; research: `technical-analysis.md`).

## Global Constraints

- Chain is target → D → P; skip repeats; P is terminal and charged without a funded check (flagged as fallback).
- Funded = active budget assignment + allocation for this user; spend/exhaustion is never considered; no reroute on exhaustion.
- D = admin default → personal project → alphabetically first project; IDP-claims mode: personal project derived by email.
- Username is the canonical personal-budget id on web and CLI (email only if username is empty) — user decision 2026-11-01, matches `budget_service` provisioning.
- Case-insensitive (`casefold`) project-name matching for membership / personal-project checks. **[Superseded: membership is exact-case (CR-008); only a CLI header maps a single case-insensitive variant to the member spelling, and the walk de-duplicates by exact name (AR-6).]**
- Cache key stays `(project_name, category_value, user_id)`, TTL `BUDGET_RESOLUTION_CACHE_TTL` (60 s); no active invalidation.
- Log style `budget_event=<name> component=<name> key=value`.
- Sync and async parity; lazy imports where neighbours use them.
- Commit message: `EPMCDME-16625: <summary>`; never stage `.codemie/codemie-cli.config.json`.
- New Python files carry the Apache 2.0 header (copy from a neighbour; `make license-check`).
- Test command: `poetry run pytest <path> -q`; lint: `make ruff`.
- Metric attribute names: existing `budget_fallback_from` (= original target), new `budget_fallback_kind` ∈ {`default_project`, `personal`}.

## Review Focus

- User with no email (stub/IDP): resolver must not raise; chain ends at P keyed by username.
- Project name differing only by case (`Team-A` vs `team-a`, email in mixed case) must be treated as the same project/personal project.
- D equals the target (member default is the asset project) → no duplicate probe, no spurious fallback flag.
- `user.default_project` is a project the user is no longer a member of / has no budget → falls to P, not an exception.
- Probe/DB error in the funded check → treat as unfunded for D and target consistently, log once, never crash the request.

---

### Task 1: Default-project derivation (case-insensitive, IDP mode)

**Files:**
- Modify: `src/codemie/rest_api/security/user.py` (`resolve_default_project`, add `same_project_name`)
- Modify: `src/codemie/rest_api/security/user_providers/legacy_jwt.py:~56` (populate `default_project` for IDP-claims users)
- Test: `tests/codemie/rest_api/security/test_user.py`, `tests/codemie/rest_api/security/user_providers/` (existing legacy_jwt test file; create `test_legacy_jwt_default_project.py` if none)

**Interfaces:**
- Produces: `same_project_name(a: str | None, b: str | None) -> bool` (casefold equality, False if either empty); `resolve_default_project(project_names, stored_default, email)` unchanged signature, now returns the member's own spelling of the personal project when email matches case-insensitively.

Test-first: yes — `test_resolve_default_project_matches_email_case_insensitively` (names `{"Dmytro_Bondarenko5@EPAM.com", "zeta"}`, email lowercased → returns the member's spelling, not "zeta"); `test_legacy_jwt_user_gets_personal_default_project` (claims user with email in `project_names` → `user.default_project` is that project; without personal project → alphabetical first).

- [ ] **Step 1:** Write the two failing tests above.
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/rest_api/security -q -k "default_project"`; expect FAIL.
- [ ] **Step 3:** Implement `same_project_name` and update `resolve_default_project` personal-project match to use it (return the matched member name).
- [ ] **Step 4:** **[Superseded: the default is never derived at login or stored. `attribution_input_for_user` derives it per request, and the UI and CLI analytics use the same derivation (`User.effective_default_project`, AR-7).]** In `legacy_jwt.py` set `user.default_project = resolve_default_project(user.project_names, None, user.email)` where the ephemeral `User` is built; keep the existing username-based personal-project ensure untouched.
- [ ] **Step 5:** Run the same pytest selection; expect PASS. Commit `EPMCDME-16625: Derive default project case-insensitively and in IDP-claims mode`.

### Task 2: Shared funded probe

**Files:**
- Modify: `src/codemie/service/budget/budget_resolution_service.py` (add `probe_project_budget_scopes_sync(project_name, user_id) -> set[BudgetCategory]` and async `probe_project_budget_scopes(...)`; both fill `_resolution_cache` for PLATFORM/CLI/PREMIUM_MODELS incl. `None`)
- Modify: `src/codemie/enterprise/litellm/llm_factory.py` `_probe_direct_project_budget_scopes` (l.688) → delegate to the sync function
- Modify: `src/codemie/enterprise/litellm/proxy_router.py` `_probe_project_budget_scopes` (l.557) / `_get_cached_project_budget_scopes` (l.539) → delegate to the async function
- Test: `tests/codemie/service/budget/test_budget_resolution_service.py`, `tests/enterprise/litellm/test_llm_factory.py`, `tests/enterprise/litellm/test_proxy_router.py`

**Interfaces:**
- Produces: `probe_project_budget_scopes_sync(project_name: str | None, user_id: str) -> set[BudgetCategory]` (empty set for falsy project; cached 60 s; negative entries cached); async twin with identical semantics. "Funded" for a project = `bool(scopes)`.

Test-first: yes — `test_probe_returns_scopes_with_active_budget_and_allocation`, `test_probe_empty_when_budget_without_allocation_for_user` (D6), `test_probe_sync_and_async_agree_and_share_cache` (second call hits `_resolution_cache`, no DB call), `test_probe_caches_negative_result`.

- [ ] **Step 1:** Write the four tests (mock the repository/session layer used today by `resolve`/`resolve_sync`; call `clear_budget_resolution_cache()` in setup).
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/budget/test_budget_resolution_service.py -q -k probe`; expect FAIL (names undefined).
- [ ] **Step 3:** Implement the sync/async probes by moving the logic out of `llm_factory._probe_direct_project_budget_scopes` and `proxy_router._probe_project_budget_scopes` (same SQL/repository call as today, one place per mode); make both old functions thin delegates, keeping their names so existing tests/patches keep working.
- [ ] **Step 4:** Run `poetry run pytest tests/codemie/service/budget tests/enterprise/litellm/test_llm_factory.py tests/enterprise/litellm/test_proxy_router.py -q`; expect PASS.
- [ ] **Step 5:** Commit `EPMCDME-16625: Share one project-budget funded probe between web and CLI paths`.

### Task 3: Attribution resolver (pure logic, matrix rows 1–16)

**Files:**
- Create: `src/codemie/service/budget/attribution_resolver.py`
- Test: `tests/codemie/service/budget/test_attribution_resolver.py`

**Interfaces:**
- Consumes: `same_project_name` (Task 1).
- Produces:
  - `class AssetKind(StrEnum): PROJECT_ASSET, NO_ASSET, CLI`
  - `class FallbackKind(StrEnum): DEFAULT_PROJECT = "default_project"; PERSONAL = "personal"`
  - `@dataclass(frozen=True, slots=True) class AttributionInput`: `kind: AssetKind`, `asset_project: str | None`, `is_global: bool`, `email: str`, `member_projects: frozenset[str]` (member ∪ admin), `default_project: str | None`, `cli_project: str | None = None`
  - `@dataclass(frozen=True, slots=True) class AttributionDecision`: `charged_project: str | None` (None ⇒ personal budget), `targeted_project: str | None`, `fallback_kind: FallbackKind | None`, `ignored_cli_project: str | None = None`
  - `def select_target(inp: AttributionInput) -> tuple[str | None, str | None]` → (target, ignored_cli_project); target None means "D"
  - `def resolve_attribution(inp, is_funded: Callable[[str], bool]) -> AttributionDecision`
  - `async def aresolve_attribution(inp, is_funded: Callable[[str], Awaitable[bool]]) -> AttributionDecision`

Test-first: yes — one parametrized `test_matrix_row[...]` per matrix row 1–16 with assertions on `(charged_project, fallback_kind)`: row 6 member + global + funded D → D (not asset project); row 4 member + unfunded asset project + funded D → D with `targeted_project=asset`, `FallbackKind.DEFAULT_PROJECT`; unfunded D → `charged_project is None`, `FallbackKind.PERSONAL`; row 5 non-member shared → asset project never probed (assert `is_funded` never called with it); row 2 D == personal and unfunded → P; row 16 → `ignored_cli_project` set; plus `test_async_matches_sync`, `test_probe_error_counts_as_unfunded`, `test_case_insensitive_membership`, `test_no_email_user_does_not_raise`.

- [ ] **Step 1:** Write the parametrized matrix test and the four edge tests (stub `is_funded` with a set of funded project names; record calls).
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/budget/test_attribution_resolver.py -q`; expect FAIL (module missing).
- [ ] **Step 3:** Implement `select_target` per the ticket's decision chart (global → D; project asset: member (casefold) → asset project else D; asset project that is the user's personal project → D; NO_ASSET → D; CLI: header member → header project, non-member → D with `ignored_cli_project`, none → D) and `resolve_attribution`: walk `[target, D]` de-duplicated (casefold) **[Superseded: membership is exact-case (CR-008); only a CLI header maps a single case-insensitive variant to the member spelling, and the walk de-duplicates by exact name (AR-6).]**, return first funded as the charge, else personal; `fallback_kind` None when first step charged, `DEFAULT_PROJECT` when charge is D after a different target, `PERSONAL` when P. Wrap `is_funded` exceptions: log `budget_event=funded_probe_failed component=attribution_resolver` and treat as unfunded. Implement the async twin sharing the pure helpers.
- [ ] **Step 4:** Run the same command; expect PASS.
- [ ] **Step 5:** Commit `EPMCDME-16625: Add attribution resolver implementing the target-default-personal chain`.

### Task 4: Web path wiring and context fields

**Files:**
- Modify: `src/codemie/rest_api/models/settings.py:194` (`LiteLLMContext`: add `budget_fallback_kind: str | None = None`, `budget_target_project: str | None = None`) **[Superseded: no separate `budget_target_project` field; the target travels in `budget_fallback_from`, and the walk's project steps in `budget_candidates` (AR-1).]**
- Modify: `src/codemie/service/llm_service/utils.py` (`_resolve_effective_project`, `_unfunded_project`, `set_llm_context`)
- Modify: `src/codemie/enterprise/litellm/llm_factory.py` (`_record_direct_budget_attribution` l.531, `_configure_direct_runtime_overrides` l.550) to consume `budget_fallback_kind`/charged project instead of re-deriving a personal-only fallback
- Modify: `src/codemie/core/dependecies.py` (add `get_budget_fallback_kind() -> str | None`)
- Test: `tests/codemie/service/llm_service/test_utils_litellm_context.py`, `tests/enterprise/litellm/test_llm_factory.py`, `tests/codemie/rest_api/models/test_settings_litellm.py`

**Interfaces:**
- Consumes: `resolve_attribution`, `AttributionInput`, `AssetKind` (Task 3); `probe_project_budget_scopes_sync` (Task 2).
- Produces: `set_llm_context(asset, fallback_project_name, user, llm_model=None)` unchanged signature; sets `LiteLLMContext(current_project=<charged project or user.email>, personal_project=user.email, budget_fallback_from=<targeted project if fallback>, budget_fallback_kind=..., budget_target_project=<target>)`. **[Superseded: no separate `budget_target_project` field; the target travels in `budget_fallback_from`, and the walk's project steps in `budget_candidates` (AR-1).]**

Test-first: yes — `test_marketplace_assistant_member_charges_default_project` (D funded → `current_project == D`, `budget_fallback_kind is None`), `test_marketplace_nonmember_funded_default` (16466), `test_default_unfunded_falls_to_personal_with_kind_personal`, `test_shared_project_assistant_nonmember_never_charges_asset_project`, `test_personal_assistant_owner_with_team_default_charges_default`, `test_member_unfunded_project_falls_to_default_then_personal`, `test_personal_creds_bypass_unchanged`, `test_shared_asset_flag_cannot_charge_nonmember_project`.

- [ ] **Step 1:** Replace/extend the existing `TestResolveEffectiveProjectSharing` cases with the eight tests above (patch `SettingsService`, `set_litellm_context`, and the probe to a set of funded projects).
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/llm_service/test_utils_litellm_context.py -q`; expect FAIL.
- [ ] **Step 3:** In `set_llm_context` build `AttributionInput` from `asset`/`user` (`AssetKind.NO_ASSET` when `asset is None`; resolve `is_global`, project), call `resolve_attribution` with `is_funded=lambda p: bool(probe_project_budget_scopes_sync(p, user.id))` (skip probing and keep today's behaviour when `litellm_creds`, no `user.id`, not `lite_llm` mode, or no `user.email`); keep the category-aware refinement from `_unfunded_project` for the model-known case by passing it as the `is_funded` implementation. Delete the `is_global`-only branch in `_resolve_effective_project`; keep `_is_shared_asset_creds_override` but ensure it only nulls personal creds (never selects a project).
- [ ] **Step 4:** Add the two `LiteLLMContext` fields and `get_budget_fallback_kind`; update `llm_factory._record_direct_budget_attribution`/`_configure_direct_runtime_overrides` so a default-project charge is applied to `context.current_project` with `budget_fallback_from=<target>` and kind `default_project`, and personal charge keeps `current_project == personal_project`.
- [ ] **Step 5:** Run `poetry run pytest tests/codemie/service/llm_service tests/enterprise/litellm/test_llm_factory.py tests/codemie/rest_api/models/test_settings_litellm.py -q`; expect PASS. Commit `EPMCDME-16625: Route web budget attribution through the shared resolver`.

### Task 5: Nested, asset-less and indexing call sites

**Files:**
- Modify: `src/codemie/service/assistant_service.py:~841` (`build_agent_for_workflow`) and any other `set_llm_context` caller that overwrites the parent context (audit all sites listed in `technical-analysis.md` §2)
- Modify: `src/codemie/core/dependecies.py` (add a context manager `scoped_litellm_context()` that restores the previous `litellm_context` on exit, if none exists)
- Verify/adjust: `src/codemie/rest_api/routers/assistant/generation.py`, `_shared.py`, `routers/skill.py`, `routers/workflow.py`, `workflow_executions.py`, `workflows/workflow.py:996`, `datasource/base_datasource_processor.py:287`, `datasource/file/file_datasource_update_processor.py:197`
- Test: `tests/codemie/service/assistant/test_langgraph_assistant.py` (or `tests/codemie/service/test_assistant_service*.py` where `build_agent_for_workflow` is covered), `tests/codemie/datasource/test_base_datasource_processor_llm_context.py`, `tests/codemie/service/llm_service/test_utils_litellm_context.py`

**Interfaces:**
- Produces: `scoped_litellm_context() -> ContextManager[None]` in `core/dependecies.py`.

Test-first: yes — `test_workflow_node_assistant_resolved_per_its_project_then_parent_context_restored` (parent workflow charged D; node assistant from another project, non-member → D; after build, `get_current_project()` equals the parent's value), `test_multi_hop_sub_assistant_resolves_independently`, `test_no_asset_flows_target_default_project` (generation/skill/workflow execution with funded D → D; unfunded D → personal kind `personal`), `test_datasource_indexing_nonmember_does_not_charge_index_project`.

- [ ] **Step 1:** Write the four tests.
- [ ] **Step 2:** Run them (`poetry run pytest <the files above> -q -k "restored or multi_hop or no_asset or indexing_nonmember"`); expect FAIL.
- [ ] **Step 3:** Add `scoped_litellm_context` (contextvar token reset) and use it around nested `set_llm_context` use in `build_agent_for_workflow`; for the no-asset routers pass `asset=None` and rely on `AssetKind.NO_ASSET` → D (do not pass a request-supplied project as target; ticket row 12). Confirm datasource processors pass the `IndexInfo` so rows 3–5/13 apply.
- [ ] **Step 4:** Run the same command plus `poetry run pytest tests/codemie/datasource tests/codemie/rest_api/routers -q -k "llm_context or attribution"`; expect PASS.
- [ ] **Step 5:** Commit `EPMCDME-16625: Scope nested assistant attribution and apply the chain to asset-less flows and indexing`.

### Task 6: Trigger owner resolution

**Files:**
- Modify: `src/codemie/triggers/bindings/utils.py` (`resolve_trigger_user`)
- Modify: `src/codemie/triggers/bindings/webhook.py:~526`, `src/codemie/triggers/actors/datasource.py:~1153` (replace bare `User(id=…)` with `resolve_trigger_user`)
- Test: `tests/codemie/triggers/bindings/test_utils.py` (create if absent), `tests/codemie/triggers/bindings/test_webhook.py`, `tests/codemie/triggers/actors/test_datasource.py`

**Interfaces:**
- Consumes: `user_project_repository.get_by_user_id(session, user_id) -> list[UserProject]`, `resolve_membership_default(rows, email)`.
- Produces: `resolve_trigger_user(user_id: str) -> User` now populated with `project_names`, `admin_project_names`, `default_project`.

Test-first: yes — `test_resolve_trigger_user_loads_memberships_and_default`, `test_resolve_trigger_user_db_failure_returns_stub`, `test_webhook_workflow_uses_resolved_owner`, `test_datasource_trigger_uses_resolved_owner`.

- [ ] **Step 1:** Write the four tests (patch `get_session`, `user_repository`, `user_project_repository`).
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/triggers -q -k "resolve_trigger_user or resolved_owner"`; expect FAIL.
- [ ] **Step 3:** Extend `resolve_trigger_user` with the membership load (same session) and default via `resolve_membership_default`; keep the stub fallback on exception. Replace the two stub constructions.
- [ ] **Step 4:** Run `poetry run pytest tests/codemie/triggers -q`; expect PASS.
- [ ] **Step 5:** Commit `EPMCDME-16625: Attribute scheduled and webhook triggers as the owner with real memberships`.

### Task 7: CLI/IDE proxy on the resolver, one personal identity

Identity decision (2026-11-01): **username** is the personal-budget customer id (what `budget_service` provisions). CLI already uses it; web LLM construction switches its id source from the logging email to the current user's username (email only if username is empty). No provisioning change, no migration.

**Files:**
- Modify: `src/codemie/core/dependecies.py` (`_resolve_user_email` → personal identity from the current user: `username or email`; used by `get_llm_by_credentials` / `get_embeddings_model`)
- Modify: `src/codemie/enterprise/litellm/proxy_router.py` (`_extract_request_info` l.454, `_flag_personal_budget_fallback` l.618 + caller, header membership check)
- Modify: `src/codemie/service/monitoring/metrics_constants.py` (add `BUDGET_FALLBACK_KIND = "budget_fallback_kind"`), `src/codemie/service/monitoring/llm_proxy_monitoring_service.py:~470-520`
- Test: `tests/enterprise/litellm/test_proxy_router.py`, `tests/codemie/core/test_dependencies_litellm_context.py` (or nearest), `tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py`

**Interfaces:**
- Consumes: `aresolve_attribution`, `AssetKind.CLI` (Task 3); async `probe_project_budget_scopes` (Task 2); `personal_workspace_names` (Task 1 fix-up).
- Produces: `async def _apply_budget_attribution(user, request_info) -> AttributionDecision` — sets `request_info[PROJECT]` to the charged project (target or D; `user.username` when personal **[Superseded: when P pays, `request_info[PROJECT]` is the email-named personal project, as on web (7a83dd1c8); the LiteLLM customer stays the username.]**), `BUDGET_FALLBACK_FROM` = original target on fallback, `BUDGET_FALLBACK_KIND`; logs and drops a non-member header project.

Test-first: yes — `test_cli_header_project_nonmember_is_ignored_and_logged`, `test_cli_no_project_charges_funded_default`, `test_cli_member_project_unfunded_falls_to_default`, `test_cli_member_project_and_default_unfunded_falls_to_personal`, `test_web_llm_identity_is_username_not_logging_email`, `test_monitoring_project_is_charged_project_with_fallback_kind`.

- [ ] Step 1: write tests; Step 2: run RED; Step 3: implement; Step 4: run `poetry run pytest tests/enterprise/litellm tests/codemie/service/monitoring tests/codemie/core -q` GREEN; Step 5: commit `EPMCDME-16625: Route CLI proxy attribution through the shared resolver and use one personal identity`.

### Task 8: Analytics emission and readers

**Files:**
- Modify: `src/codemie/service/monitoring/base_monitoring_service.py:~48` (`_add_budget_fallback_attribute`: also set `BUDGET_FALLBACK_KIND` from `get_budget_fallback_kind()`)
- Modify: `src/codemie/service/usage_attribution/constants.py:44` (add `F_FALLBACK_KIND = "attributes.budget_fallback_kind.keyword"`), `queries.py` (`has_fb()` l.149 and its users), `money.py:~334`, `parsing.py:~104`
- Modify: `src/codemie/service/analytics/handlers/budget_usage_service.py` and `repository/cli_analytics/postgres/*` only where they read `budget_fallback_from` (grep first)
- Test: `tests/codemie/service/usage_attribution/` (existing query/money/parsing tests), `tests/codemie/service/monitoring/test_base_monitoring_service.py`

**Interfaces:**
- Produces: `has_fb()` matches records with `budget_fallback_from` AND NOT `budget_fallback_kind == "default_project"` (legacy rows without kind still match = personal).

Test-first: yes — `test_fallback_kind_attribute_emitted_from_context`, `test_has_fb_excludes_default_project_fallback` (query contains the must_not on kind), `test_has_fb_keeps_legacy_rows_without_kind`, `test_default_project_fallback_not_counted_as_paid_personally`.

- [ ] **Step 1:** Write the four tests.
- [ ] **Step 2:** Run `poetry run pytest tests/codemie/service/usage_attribution tests/codemie/service/monitoring -q -k "fallback"`; expect FAIL.
- [ ] **Step 3:** Implement emission and reader changes; do not change index mappings (free-form attributes, per spec).
- [ ] **Step 4:** Run `poetry run pytest tests/codemie/service/usage_attribution tests/codemie/service/monitoring tests/codemie/service/analytics -q`; expect PASS.
- [ ] **Step 5:** Commit `EPMCDME-16625: Record fallback kind in analytics and keep default-project fallbacks out of personal-paid counts`.

### Task 9: Docs, end-to-end matrix check and gates

**Files:**
- Create: `.ai-run/guides/integration/budget-attribution.md` (rule, resolver entry points, funded probe, email identity, analytics fields); add a row to the `AGENTS.md` Guide Imports table
- Test: `tests/codemie/service/budget/test_attribution_matrix_end_to_end.py` (rows 1–19 through `set_llm_context` and `_apply_budget_attribution`, including rows 17 trigger-owner, 18 personal key, 19 exhausted-not-rerouted)

Test-first: yes — `test_row_19_exhausted_budget_is_not_rerouted` (funded-but-exhausted target stays the charge; resolver never consults spend), `test_row_18_personal_key_bypasses_resolver`, `test_web_and_cli_same_personal_customer_id`, `test_idp_claims_mode_default_is_personal_project`.

- [ ] **Step 1:** Write the four tests; run `poetry run pytest tests/codemie/service/budget/test_attribution_matrix_end_to_end.py -q`; expect FAIL where wiring gaps remain, fix them in the owning module.
- [ ] **Step 2:** Write the guide and the `AGENTS.md` row.
- [ ] **Step 3:** Run `make ruff` and `make license-check`; fix findings.
- [ ] **Step 4:** Run the touched suites: `poetry run pytest tests/codemie/service tests/enterprise/litellm tests/codemie/triggers tests/codemie/rest_api/security tests/codemie/datasource -q`; expect PASS. (`make gitleaks` / `make test-harness` need Docker — report as unverified if blocked, per `test_harness_env_blockers` memory.)
- [ ] **Step 5:** Commit `EPMCDME-16625: Document budget attribution and add matrix end-to-end tests`. MR description (when requested) must include `## Test harness` and ask a reviewer to post `/sanity` (budget enforcement is security-relevant; CLI customer-id switch is a behavior change).
