# Technical Research

**Task**: budget ledger litellm project-spending
**Generated**: 2026-11-09
**Research path**: filesystem

---

## 1. Original Context

User request (verbatim): "investigate, identify the root cause and fix https://jiraeu.epam.com/browse/EPMCDME-16989. try to reproduce locally"
Repository: C:\Users\KostiantynPshenychny\Documents\epm-cdme\repos\codemie (Python backend). Closed-source features live in sibling repo ..\codemie-enterprise (installed into codemie as a pinned optional package); check it too if the ledger/budget/LiteLLM spend logic lives there.
Ticket EPMCDME-16989 (Bug, Major, AI-generated, so verify claims against the code):
Summary: Non-member request billed to personal customer is also counted in the named project's budget, causing ledger/LiteLLM spend mismatch.
Description: When a request names a project via the X-CodeMie-Project header but the requesting user is not a member of that project, LiteLLM correctly bills the spend to the user's personal customer. However, the platform ledger also counts the same request against the named project's budget and increases its current_spending. So the project budget's current_spending no longer matches the spend charged against the project's LiteLLM key, and the same request shows up in two budgets (personal and project).
Preconditions: a project with a configured budget and a LiteLLM key; an authenticated user who is NOT a member of it; the user can send a request with X-CodeMie-Project naming that project.
Steps: 1) as the non-member, send a request with X-CodeMie-Project: Project A; 2) LiteLLM bills the personal customer; 3) record Project A budget current_spending in the ledger before/after; 4) record LiteLLM spend on Project A's key before/after.
Expected: LiteLLM bills the personal customer; the ledger counts the spend in the personal budget only; Project A current_spending unchanged; ledger and LiteLLM agree for Project A.
Actual: the ledger also increases Project A current_spending by the same amount even though Project A's LiteLLM key was never charged.
Acceptance criteria: 1) non-member + X-CodeMie-Project + LiteLLM bills personal => ledger counts personal budget only; 2) the project's current_spending is not incremented; 3) the ledger's project spend matches the spend on the project's LiteLLM key; 4) regression: a project member's request still increments the project's current_spending; 5) regression tests cover member and non-member paths and confirm spend lands in exactly one budget.
Research focus: trace how X-CodeMie-Project is resolved (membership check, fallback to personal), how the LiteLLM key/customer is chosen, and how the ledger/budget current_spending is attributed (where project vs personal is decided, e.g. from the header, the request context, or LiteLLM spend logs). Find the root cause, where the two decisions diverge. Also note how to reproduce locally (the local stack runs via Podman; admin login admin@codemie.ai / password) and which existing tests cover this.

---

## 2. Codebase Findings

All paths relative to `repos/codemie/src/codemie/` unless prefixed `codemie-enterprise/`.

### Existing Implementations

**Purpose A — decide who pays (LiteLLM customer) for a proxied request naming a project.**
- `enterprise/litellm/proxy_router.py:_proxy_to_llm_proxy` (~1988): `_validate_project_header` → `_extract_request_info` (PROJECT = header, else `default_project`, else username, :586) → `_authorize_project_header` (:543) → `_resolve_budget_attribution_stream` (:915) → `_prepare_proxy_headers` (:1160).
- `_authorize_project_header`: 403 unless `user.has_access_to_application(name)` (`rest_api/security/user.py:168` — true for **any admin/maintainer**, project members, project admins); the membership-row recheck runs only when `ENABLE_USER_MANAGEMENT and not user.is_admin_or_maintainer`. So a regular non-member is rejected; an admin/maintainer who is not a member passes. Sets `request_info[PROJECT]` to the stored name.
- `_probe_project_budget_scopes` (:689) → `project_budget_repository.get_project_budget_categories_batch` (`repository/project_budget_repository.py:457`): inner-joins `project_member_budget_assignments` on `user_id`; a user with no allocation row (non-member, or member without an allocation for that category) gets **no scopes**.
- No project runtime → `_flag_personal_budget_fallback(..., project_budget_used=False)` (:750) sets `request_info["budget_fallback_from"]=project` and **leaves `request_info[PROJECT]` untouched** (docstring: "credential and runtime lookups still key on it"), then `_create_global_budget_body_stream` (:1048) sets `litellm_customer_id = username` (personal customer).

**Purpose B — tell the ledger which project a call belongs to.**
- `_apply_spend_logs_metadata_header` (:365) builds `x-litellm-spend-logs-metadata` with `project=_proxy_project_in_context(request_info, user)` (:418). That helper returns `request_info[PROJECT]` unless it equals username/email. **It never reads `budget_fallback_from`**, so on the fallback path `codemie_project = "Project A"` while `end_user` = personal customer.
- `service/spend/spend_logs_metadata.py:build_spend_logs_metadata` — identity triple `codemie_user_id`/`codemie_category`/`codemie_project`; project optional.
- `codemie-enterprise/src/codemie_enterprise/spend_uploader/source.py:72` reads `slm->>'codemie_project'`; `spend_uploader/attribution.py:decide` — when the triple is complete, `attr_source='explicit'`, `attr_form='project_scoped' if project else 'principal'`, `attr_project=project`; `end_user` is **not consulted**. `transform.py:79` stores it in `litellm_spend_logs`.
- Platform (non-proxy) twin already handles fallback: `service/llm_service/utils.py:set_llm_context` sets `current_project=user.email` when `_unfunded_project` flags it; `enterprise/litellm/llm_factory.py:_record_direct_budget_attribution` (:704) swaps `current_project` to `personal_project` and records `budget_fallback_from`; `_spend_attribution_values` (called :365 after `_configure_direct_runtime_overrides` :348) then gets `project=None` via `_project_in_context` (:620). Non-member global-assistant access resolves to `user.email` in `_resolve_effective_project` (`utils.py:34`).
- ES analytics twin: `service/monitoring/llm_proxy_monitoring_service.py:526` rewrites `PROJECT` to `user.username` when `BUDGET_FALLBACK_FROM` is set.

**Purpose C — compute project `current_spending`.**
- `repository/spend_logs_repository.py:predicate_for_holder` (:252): `ProjectHolder` = `attr_form='project_scoped' AND attr_project=:p AND attr_category=:c` (via `_project_scoped_match` :287). No condition on `end_user`. Consumers: `rest_api/routers/projects/crud.py:167`, `service/project/project_spending_service.py:312`, `service/analytics/handlers/member_spend_service.py:232`; `ProjectMemberHolder`/`ProjectGroupHolder` and `_project_scoped_match_any` (access narrowing) read the same columns.
- `_personal_predicate` (:330): explicit rows keyed on `attr_user_id`+category with `end_user` not a `codemie:project:` customer — so the fallback row is **also** in the personal figure.
- `service/spend/cutover.py:CutoverSpendLedger` — legacy (LiteLLM-customer) ledger below `SPEND_LEDGER_COVERAGE_FLOOR`, spend-logs ledger above it.

**Other touched code**: `service/spend/attribution.py` (codemie-side end_user parser, fallback only).

### Decisions Already in the Code
- **Double counting is recorded as intended** in `_personal_predicate` docstring (EPMCDME-15204): "A project row charged to the person is then in this figure *and* in the project's attributed one - charged to the person, attributed to the project". Alternative (project figure excludes personal-customer rows) not taken; reason given is mirroring "LiteLLM's two counters".
- `budget_fallback_from` (EPMCDME-15110/15111, commit `1d91ef34f`) predates the spend-logs metadata header (EPMCDME-13429, `9c288b290`, which added `_proxy_project_in_context`); the latter did not adopt the fallback flag. No recorded reason found.
- Explicit identity beats `end_user` parsing in the uploader (`decide`); the proxy strips client-supplied metadata header (`PROXY_HOP_BY_HOP_HEADERS`).
- Convention: category is decided once by routing and carried on `request_info[SPEND_ATTRIBUTION_CATEGORY]`; header builder must not re-derive.
- Project "key-only" mode (`_create_project_runtime_body_stream`, mode != member tracking) sets no customer id, so such project rows can carry a NULL/placeholder `end_user`.

### Task Statements Checked Against the Code
- "user is NOT a member ... can send a request with X-CodeMie-Project" — holds only for admin/maintainer non-members (or `ENABLE_USER_MANAGEMENT=false` with stale `project_names`); regular non-members get 403 at `_authorize_project_header`. Same bug fires for any member lacking an allocation in the routed category.
- "LiteLLM correctly bills the personal customer" — holds (`_create_global_budget_body_stream`).
- "ledger also counts against Project A" — holds: `codemie_project` → `project_scoped` row → `ProjectHolder`.
- "same request shows up in two budgets" — holds and is the documented intent of `_personal_predicate` (see Decisions).
- AC4 member path increments project — holds today via member customer + `codemie_project`.

### Consumers of Changed Contracts
`codemie_project` / `attr_project` readers: `ProjectHolder`, `ProjectMemberHolder`, `ProjectGroupHolder`, `_project_scoped_match_any` (access boundary), `spend_logs_model.py` indexes and `litellm_spend_hourly` rollup (`attr_project` dimension), `member_spend_service.py`, `projects/crud.py`, `project_spending_service.py`. `request_info[PROJECT]` readers after fallback: `resolve_litellm_user_credentials`, `_resolve_project_budget_runtime`, monitoring `_sanitize_request_info`.

### Architecture and Layers Affected
Enterprise LiteLLM proxy router (attribution header); spend metadata builder; enterprise spend uploader (attribution decide); spend ledger repository predicates.

### Integration Points
LiteLLM proxy (`x-litellm-customer-id`, `x-litellm-spend-logs-metadata` → `LiteLLM_SpendLogs.metadata`); codemie-enterprise spend uploader job (`LITELLM_SPEND_UPLOADER_*`) copying into codemie `litellm_spend_logs`.

### Patterns and Conventions
Pure helper functions in `proxy_router.py` taking `request_info`/`user`; identity triple all-or-nothing; docstring-heavy rationale comments.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (CLI analytics guides mention the header; none cover ledger attribution). Prior task docs: `docs/superpowers/tasks/2026-09-30-removed-project-budget-spend/`, `2026-10-07-epmcdme-15623/`.

### Architectural Decisions
Inline: `_personal_predicate` (EPMCDME-15204), `_flag_personal_budget_fallback` (EPMCDME-15111 AC6/AC10), `_project_in_context` docstring (personal project must not be sent as `codemie_project`).

### Derived Conventions
Fallback is represented by `budget_fallback_from`; the project name stays in `request_info[PROJECT]` for lookups.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/enterprise/litellm/test_proxy_router.py`: `TestFlagPersonalBudgetFallback` (:3428), `TestResolveBudgetAttributionStream` (:1246), `TestPrepareProxyHeaders` (:902), `TestValidateProjectHeader` (:189), `TestProxyToLLMProxy` (:2152).
- `tests/enterprise/litellm/test_spend_logs_metadata_transport.py`: `TestTheProxyStripsWhatTheClientSentAndSetsItsOwn` (:211) — username/email not sent as project; platform `TestTheProjectInContextIsNotAlwaysAProject` (:42).
- `tests/codemie/service/spend/test_legacy_ledger.py`; uploader tests in `codemie-enterprise/tests/`.

### Testing Framework and Patterns
pytest + pytest-asyncio, `SimpleNamespace` users, `patch.object(proxy_router, ...)`, `metadata_in(headers)` helper.

### Coverage Gaps
No test asserts `codemie_project` is omitted when `budget_fallback_from` is set; no end-to-end member vs fallback test that a row lands in exactly one holder.

---

## 5. Configuration and Environment

### Environment Variables
Local `.env`: `ENABLE_USER_MANAGEMENT=true`, `LLM_PROXY_ENABLED=true`, `LLM_PROXY_MODE=lite_llm`, `LITELLM_SPEND_UPLOADER_ENABLED=true`. `config.py:747` `SPEND_LEDGER_COVERAGE_FLOOR="2026-11-01T00:00:00Z"`, `SPEND_LEDGER_IDENTITY_FLOOR`, `SPEND_LEDGER_LEGACY_BELOW_FLOOR`.

### Configuration Files
`configs/config.py` (`LITE_LLM_PROXY_ENDPOINTS` :812, e.g. `POST /v1/chat/completions`).

### Feature Flags and Deployment Concerns
`LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED` (platform path only). Local repro: log in as `admin@codemie.ai` (admin passes `_authorize_project_header` without membership), ensure admin has no allocation in Project A, `POST /v1/chat/completions` with `X-CodeMie-Project: <Project A>`; check log `budget_attribution_fallback`, LiteLLM `LiteLLM_SpendLogs.metadata->'spend_logs_metadata'->>'codemie_project'`, then after uploader run (every 2 min) `litellm_spend_logs.attr_form/attr_project/end_user` and Project A spend from projects list endpoint.

---

## 6. Risk Indicators

- Root cause: `_proxy_project_in_context` (`proxy_router.py:418`) ignores `budget_fallback_from`, diverging from `_flag_personal_budget_fallback` and from the platform path (`llm_factory.py:_record_direct_budget_attribution`).
- `_personal_predicate` docstring records double counting as intended (EPMCDME-15204) — the ticket contradicts a documented decision; see Decisions.
- Speculative: fixing at the predicate level (`ProjectHolder` requiring a project customer) would affect project key-only rows with NULL/placeholder `end_user` and every `attr_project` consumer listed above.
- Own-credentials bypass (`_resolve_budget_attribution_stream` :938) also sends `codemie_project` while no project key is charged; same predicate counts it.
- Historical rows already stored `project_scoped`; a header fix does not rewrite them.
- Reproduction depends on the uploader job and the coverage floor; regular non-members cannot reproduce (403).

---

## 7. Summary for Complexity Assessment

The divergence is in one place: the enterprise LiteLLM proxy router decides who pays (`_create_global_budget_body_stream` → personal customer, flagged by `_flag_personal_budget_fallback`) and, separately, what project the spend row names (`_proxy_project_in_context`, which ignores the fallback flag). The codemie-enterprise uploader trusts the explicit `codemie_project` key over `end_user`, producing a `project_scoped` row that the ledger's `ProjectHolder` predicate counts toward the project's `current_spending`, while `_personal_predicate` also counts it for the person. The platform (assistant) path already suppresses the project on fallback, so the proxy path is the outlier.

Layers touched: proxy router helper (one function), possibly the ledger predicate in `spend_logs_repository.py`. Surface is small and the pattern exists already (platform `_project_in_context` + `budget_fallback_from`). Tests are dense around both helpers (`test_proxy_router.py`, `test_spend_logs_metadata_transport.py`) but none covers fallback + metadata together.

Key risks: a documented EPMCDME-15204 decision accepts the double count; any predicate-level change ripples to all `attr_project` consumers; already-stored rows stay misattributed; local repro requires an admin non-member and the uploader job.

---

## 8. External References

None named by the task (Jira URL not readable from the filesystem; ticket text in Section 1 used as given).
