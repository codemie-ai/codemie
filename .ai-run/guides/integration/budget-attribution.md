# Budget Attribution

Which budget pays for an LLM request (EPMCDME-16625). One rule for web and CLI/IDE traffic.

## Rule

Pick a **target**, then walk **target → default project (D) → personal budget (P)** and charge the first step that is **funded**. Repeated steps (exact name; projects differing only in case are different budgets) are skipped; P is the last resort and is never checked.

The walk is decided once per request and walked **again** where the model is built, because the funded check probes one model's category and the model actually built (routed, premium, a sub-model) can be in another. Both re-walks take the same steps and reach D before P: web model construction walks `LiteLLMContext.budget_candidates`, and the CLI stream tries the decision's later project steps when the charged project has no runtime for the request.

| What started the request | Target |
|---|---|
| Marketplace (`is_global`) assistant or workflow | D |
| Project assistant / workflow / datasource, user is a member | The asset's project |
| Same, user is not a member (shared with them) | D |
| Asset in the user's own personal project | D |
| No asset: skill run, workflow execution, generation | D |
| CLI with no project / member project | D / that project |
| IDE or inline virtual assistant without a named project | D (billed as asset-less; its project, `user.current_project`, still selects credentials) |
| CLI naming a project the user cannot use | Rejected with 403 before attribution (`_authorize_project_header`, EPMCDME-16276). An admin or maintainer who may use the project but is not a member of it: D, with the header logged as ignored. The header project is also kept out of the credentials lookup, so the call never runs on that project's key |
| Workflow node or sub-assistant using a saved assistant | The rows above, applied to that assistant for the user who started the run |

- **Funded** = an active project budget plus an allocation for this user, in the category the request uses. Spend is never considered: an exhausted budget stays the charge and LiteLLM blocks (hard limit) or warns (soft limit). It is never rerouted.
- **Funding unknown** (the check failed, e.g. a database error) counts as unfunded at decision time: the walk moves on and the decision says so (`funding_unknown`, also in the fallback log line). The runtime then checks every project step again from the target, on web and CLI alike (`_runtime_candidates`, `_runtime_steps`), so a passing error does not move spend to P. A check that fails again at runtime fails the request. On the CLI a connection-pool checkout timeout raises `FundingCheckAborted` instead and the request fails at once with HTTP 503, since every later step would wait for the same pool; the user's budget ids are read once per request.
- **D** = the admin-assigned default, else the user's personal project, else the first of their projects by name (`resolve_default_project`). IDP-claims mode stores no default: `attribution_input_for_user` derives it (`derive_idp_default_project`) and never writes it to `user.default_project`, because credential lookup reads that field and must not change. There the username-named workspace also counts as personal. The UI's default (`/v1/user`), CLI analytics and the billing of a virtual assistant without a project use the same derivation through `User.effective_default_project()`; `User.current_project` keeps the older derivation because asset-less calls look up credentials under it (matrix row 18).
- **Own key** (personal LiteLLM credentials) pays by itself; attribution does not apply.

## Where it lives

| Concern | Code |
|---|---|
| Decision (pure, sync + async) | `service/budget/attribution_resolver.py`: `attribution_input_for_user`, `resolve_attribution`, `aresolve_attribution` |
| Funded check (one SQL, 60 s cache incl. "no budget") | `service/budget/budget_resolution_service.py`: `probe_project_budget_scopes(_sync)`; SQL `PROJECT_BUDGET_FUNDED_SQL` |
| Web / in-app | `service/llm_service/utils.py:set_llm_context` → `LiteLLMContext` (with `budget_candidates`); model construction walks them again in `enterprise/litellm/llm_factory.py:_configure_direct_runtime_overrides` (`_runtime_candidates`) |
| CLI / IDE proxy | `enterprise/litellm/proxy_router.py:_apply_budget_attribution` (category-aware via `_project_funds_request`), then `_resolve_budget_attribution_stream` (`_runtime_steps`). `_authorize_project_header` writes the header project into the request only for members |
| Nested assets | `core/dependecies.py:scoped_llm_context`, used by sub-assistant prebuilds, workflow member builds and `BaseNode.__call__` |
| Trigger owners | `triggers/bindings/utils.py:resolve_trigger_user` and `LocalIdp` load the owner through one loader, `service/user/user_by_id_loader.py`. Scheduled jobs resolve the owner when they fire (`triggers/bindings/cron.py`), never from the user frozen into the job at scheduling time. An owner whose snapshot is past the membership trust window keeps their identity without memberships, so their own personal budget pays. An owner who cannot be loaded at all runs as `stand_in_user`: no memberships, so the personal budget pays under the id-named customer and is reported under that name; each run logs `budget_event=stand_in_owner` once (`is_trigger_owner_active` only checks) |
| Indexing | Attributed like any project asset, also when `LLM_PROXY_EMBEDDINGS_DISABLED` sends embeddings to native providers: its summary and document chat models still go through LiteLLM. With native embeddings the funded check reads the default chat model |
| IDP-claims snapshot | In IDP-claims mode there are no users rows: a sign-in through an IdP that declares `BaseIdp.validates_token` (the JWKS wrapper and the enterprise providers; `False` by default and on `LocalIdp`) stores the claims in `idp_user_snapshots` (`service/user/idp_user_snapshot_service.py`). Only trigger owner resolution and HMAC-signed loopback calls read it, never a header-only `LocalIdp` request. It restores memberships but never project-admin rights. Memberships are trusted for `IDP_USER_SNAPSHOT_MAX_AGE_HOURS` (default 24) after the last login; the identity is kept for `IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS` (default 30), and older rows are pruned on the write path at most hourly per pod. Snapshots assume signature-verified IdP tokens (`JWKS_VALIDATION_ENABLED` or a validating gateway): do not run IDP-claims mode without it. Startup logs `idp_snapshot_trust` when IDP-claims mode runs without JWKS validation |

## Identity and analytics

- The personal LiteLLM customer id is the **username** on web and CLI: `budget_service` provisions personal budgets on `db_user.username`. Personal *project* names stay email-based; they are a different thing.
- Metrics: `project` is the project that was charged. When P paid it is the email-named personal project on web and CLI alike (the username if the user has no email), while the LiteLLM customer stays the username. On a fallback, `budget_fallback_from` holds the originally targeted project and `budget_fallback_kind` is `default_project` or `personal`. The targeted project is the asset's (or header's) project only for its members; otherwise it is D. So a non-member's run of another project's shared asset is reported under their own D, not under that project.
- `budget_funding_unknown=true` marks a fallback decided while a funded check failed; a request the target paid after all is not marked (`LiteLLMContext.budget_funding_unknown` on web, `request_info` on the CLI), so such rows can be filtered.
- An agent builds its model once and sets the context again before every call. A re-decision that cannot read funding keeps the payer model construction recorded (`budget_outcome_built`). Routed chats whose candidate models fall in different budget categories report the last built model's payer in metrics; spend logs stay per model. This is an accepted limitation, with no fix planned under EPMCDME-16625.
- Deployment tags (`x-litellm-tags`) follow `LiteLLMContext.routing_project`, the project the request runs for, not the charged `current_project`: attribution moves the charge, never the request to another project's deployments.
- Usage attribution counts a record as paid personally only when its kind is not `default_project`; records written before the kind existed count as personal.

**Accepted risk:** a background run can keep a revoked IDP membership for up to `IDP_USER_SNAPSHOT_MAX_AGE_HOURS` after the owner's last login.

| Avoid | Prefer |
|---|---|
| Deciding a project or fallback at a call site | Pass the asset to `set_llm_context` (or `None` for asset-less flows) and let the resolver decide |
| Building a nested assistant without restoring the caller's context | Wrap it in `scoped_llm_context()`: model headers are fixed when the model is built |
| Reading `budget_fallback_from` alone as "paid personally" | Check `budget_fallback_kind` too (`usage_attribution.queries.has_fb`) |
| A fall-through that goes from a project straight to the personal budget | Walk the decision's project steps first (`budget_candidates`, `_runtime_steps`) |
| Comparing fallback kinds with string literals | `FallbackKind` from `service/budget/attribution_resolver.py` |
| Naming an outcome (fallback from/kind) at a call site | `charge_outcome(steps, paying_step)` from the resolver |
| Keying a personal LiteLLM customer on the email | Use the username, as provisioning does |
