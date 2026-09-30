# Spec: Startup Provisioning of the `codemie-projects` LiteLLM Team

**Ticket**: EPMCDME-15072
**Scope**: codemie core only (codemie-enterprise sibling package is out of scope)

## Problem

New CodeMie deployments never create the "Codemie Projects Team" in LiteLLM. Because the
team doesn't exist, project-scoped LiteLLM keys get created without a consistent team context,
which causes incorrect budget assignment and spend attribution on the CodeMie Projects UI.

The codemie-enterprise sibling package already implements the team-provisioning logic
(`LiteLLMService.ensure_team_exists()` / `_team_exists()` / `_create_team_idempotent()`) and
already guards key minting on it inside `_generate_project_key()`. What's missing on the
codemie-core side is a call to trigger that provisioning eagerly at startup, so the team exists
before any key is ever minted, for every new deployment.

## What

Add one eager, best-effort call to `litellm_service.ensure_team_exists()` inside
`_initialize_enterprise_services` in `src/codemie/rest_api/main.py:286-318`, directly after
`litellm_service` is initialized and **before** both the LiteLLM proxy-provider registration and
the budget-provider registration blocks.

- **Ordering rationale (reviewer feedback)**: provisioning the team first means the
  budget-provider registration block — and any other budget/project-related registration or
  activity later in the same function — already has a guaranteed-existing team to operate
  against, rather than relying purely on the enterprise-side lazy guard in
  `_generate_project_key()` as the sole backstop.
- **Gate**: `litellm_service is not None and config.LLM_PROXY_BUDGET_CHECK_ENABLED` — the same
  two-part condition already used by the budget-provider registration block that now follows it.
  No new config flag is introduced.
- **Fail-open**: wrap the call in `try/except Exception`, logging a warning on failure only.
  Mirror the existing pattern at `main.py:152-171` (`_initialize_litellm_models`) and the three
  other fail-open startup blocks in the same file (`_bootstrap_superadmin`,
  `_run_keycloak_migration`, `_check_sharepoint_pkce_redis`). A failure here must never prevent
  the application from starting, and must not prevent the budget-provider registration block
  that follows it from running.
- **Reuse the existing local variable**: call through the `litellm_service` local already
  constructed earlier in the same function (`main.py:287`) — do not reintroduce it via
  `get_global_litellm_service()` or the enterprise loader.

## Why this shape

- `litellm_service is not None` is already how every other optional-enterprise block in this
  function protects itself when `INSTALL_ENTERPRISE` is unset or LiteLLM is unconfigured
  (`initialize_litellm_from_config()` returns `None` in both cases via `is_litellm_enabled()`).
  No separate `INSTALL_ENTERPRISE` check is needed at the new call site.
- Placing the call before the budget-provider block, under the same flag, was moved up from the
  originally-agreed "immediately after" position per reviewer feedback on the `spec.approved`
  gate: sequencing team provisioning first gives every subsequent budget/project-related step in
  this function a guaranteed-existing team to operate against, instead of depending solely on the
  enterprise-side lazy guard as backstop.
- It also precedes the proxy-provider registration, because that provider owns project-key
  lifecycle and is itself a project-related step. Neither registration depends on the team call
  (both only register adapters), so ordering relative to them has no runtime effect at startup.
- The actual create-vs-skip / idempotency logic is entirely the responsibility of
  `ensure_team_exists()` in codemie-enterprise. This repo's contribution is solely the call site;
  no assumption about that method's signature or internals is made here.

## Acceptance Criteria

- AC1: When `litellm_service` is available and `LLM_PROXY_BUDGET_CHECK_ENABLED` is true,
  `_initialize_enterprise_services` calls `litellm_service.ensure_team_exists()` once per
  startup, after `litellm_service` is initialized and **before** the proxy-provider and
  budget-provider registration blocks.
- AC2: Startup is idempotent — running it repeatedly (e.g. on redeploy or restart) never raises
  out of `_initialize_enterprise_services` and never depends on the team not already existing;
  duplicate-creation avoidance is delegated to `ensure_team_exists()` itself.
- AC3: If `ensure_team_exists()` raises, the exception is caught, a warning is logged, and
  startup proceeds unaffected — no other part of `_initialize_enterprise_services` (including the
  budget-provider registration that follows it) or app startup is impacted.
- AC4: When `litellm_service is None` (enterprise not installed, or LiteLLM not configured) or
  `LLM_PROXY_BUDGET_CHECK_ENABLED` is false, the new call is skipped entirely — no behavior
  change for those deployments beyond what already happens today.

## Non-goals

- Implementing or modifying `ensure_team_exists()`, `_team_exists()`, `_create_team_idempotent()`,
  or the key-minting guard inside `_generate_project_key()` — all already implemented in the
  codemie-enterprise sibling repo.
- Any deployment automation, Helm/Terraform changes, or runbook/documentation updates (ticket
  AC #7 — tracked separately).
- Introducing a new, dedicated config flag for team provisioning distinct from
  `LLM_PROXY_BUDGET_CHECK_ENABLED` — this spec reuses the existing flag by design.
- Associating existing/historical project keys retroactively with the team, or any correction of
  past spend-attribution data.
- Any router, API, persistence, or UI change — this is a startup-sequence-only change.

## Open Risks

- `LLM_PROXY_BUDGET_CHECK_ENABLED` conflates "budget enforcement" with "team provisioning"; a
  deployment that disables budget checking will also silently never get the team provisioned via
  this eager path (it still gets the enterprise-side lazy guard in `_generate_project_key()` as a
  backstop, per the ticket's AC1 framing).
- The exact signature and idempotency guarantees of `ensure_team_exists()` cannot be verified
  from this repo; implementation must confirm them against the codemie-enterprise sibling
  package before wiring the call.
- `_initialize_enterprise_services` has no existing direct unit test; verifying AC2/AC3 will
  require new test coverage authored during implementation, including a case that confirms the
  budget-provider registration block still runs when the new `ensure_team_exists()` call raises.
