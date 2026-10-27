# Technical Analysis — Lazy budget assignment with explicit project path

research_path: filesystem
layers: service, integration/provider, repository
risks: 6
external_refs: none named

## 1. Overview

This task touches three layers: the service layer (`ProjectAssignmentService._sync_project_budget_member_added`, `ProjectBudgetService.rebalance_project_budget`/`override_member_allocation`/`_resync_member_allocation`/`_sync_fixed_allocation`), the integration/provider layer (`project_member_runtime_sync.ensure_project_member_runtime_ready`, `budget_provider_adapter.sync_member_allocation`/`provision_global_user`), and the repository layer (`project_member_budget_assignment_repository`). The root cause is architecturally clear from this repo's code: new project members get a DB-only allocation row at add time (explicitly logged `sync_status=pending_provider_sync`), and the project-scoped LiteLLM customer/budget record is only created lazily, gated on an actual LLM request reaching one of two call sites (`llm_factory.py`, `proxy_router.py`). Admin rebalance/override operations assume the provider record already exists and, in several code paths, swallow provider-sync exceptions per member rather than surfacing or self-healing them, so a member who only logged in has neither a provider record nor a guaranteed error signal pointing at the real gap.

The exact failure point that produces the LiteLLM "end-user ... does not exist in db" error at rebalance/override time could not be fully confirmed from this repository alone, since the actual LiteLLM API calls live in the proprietary `codemie-enterprise` package (not vendored here) and the service-layer code that wraps them is fail-open in the paths most likely to be hit by rebalance. This is a genuine open question the ticket itself calls out ("precise failure point needs confirmation") and will need either enterprise-package source/docs or live reproduction to pin down exactly where the hard failure surfaces versus where it's currently swallowed.

Test coverage for the adjacent, already-working flows (first-use lazy sync, rebalance, override, clear-override) is substantial and uses consistent pytest/pytest-asyncio patterns with provider mocking, which gives a fix a solid scaffold to extend — but there is no existing test for the specific "allocation exists, provider_member_ref absent, rebalance/override invoked" scenario the acceptance criteria require, and acceptance criterion 5 (no duplicate/cross-project allocation) implies new assertions around the deterministic id-builders and the `codemie:project:...` naming convention. Key risk factors: the undocumented/implicit nature of the lazy-provisioning contract (no guide, docstrings only), two call sites that must both be covered or consciously scoped out, the fail-open/fail-closed inconsistency across the call chain, and the out-of-repo enterprise package boundary that limits how deep this research could go into the actual LiteLLM API semantics.

## 6. Risk Indicators

- Speculative: fixing this correctly likely requires either (a) making `_sync_project_budget_member_added` eagerly call `provider.sync_member_allocation` for the newly added member using the project's explicit path, or (b) making `rebalance_project_budget`/`override_member_allocation` detect an unsynced allocation and provision it on demand before attempting the provider update — the actual code change is a design decision for spec/plan, not established fact from this research.
- The proprietary `codemie-enterprise` package's `sync_project_member_budget_assignment`/`get_or_create_customer_with_budget` implementations are not in this repository, so the precise create-vs-update semantics producing the "does not exist in db" error cannot be confirmed from source alone.
- `_resync_member_allocation`/`_sync_created_member_allocations` are fail-open while `ensure_project_member_runtime_ready` is fail-closed; the ticket's "rebalance fails" symptom may originate from a layer not fully traced in this research.
- Two non-interchangeable provisioning identities (global username-keyed customer vs. project-scoped `codemie:project:...` customer) coexist; a fix addressing only one of the two LLM-request call sites (`llm_factory.py` vs `proxy_router.py`) would leave a gap.
- Sync/async bridging code in `project_member_runtime_sync.py` is intricate (deadlock-avoidance); any new eager-provisioning call from `ProjectAssignmentService` needs the same discipline.
- No regression tests exist today for the "member added, never used, admin rebalances/overrides" scenario.

## 7. Codebase Findings — Key File Paths

- `src/codemie/service/project/project_assignment_service.py`
- `src/codemie/enterprise/litellm/project_member_runtime_sync.py`
- `src/codemie/enterprise/litellm/budget_provider_adapter.py`
- `src/codemie/enterprise/litellm/llm_factory.py`
- `src/codemie/enterprise/litellm/proxy_router.py`
- `src/codemie/service/budget/project_budget_service.py`
- `src/codemie/service/budget/budget_resolution_service.py`
- `src/codemie/service/user/user_management_service.py`
- `tests/enterprise/litellm/test_project_member_runtime_sync.py`
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`
- `tests/codemie/service/project/test_project_assignment_service.py`

## 8. External References

None named by the task.
