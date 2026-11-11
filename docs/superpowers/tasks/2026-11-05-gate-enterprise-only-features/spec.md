# Spec: Enterprise Feature Gating by Package Presence (EPMCDME-14536)

## Problem

Cost Centers, Budget Management, and Chargeback are controlled solely by YAML booleans
(`features:costCenters`, `features:budgetManagement`, `features:projectChargeback` in
`customer-config.yaml`), with no check for whether `codemie-enterprise` is installed. A
customer config can therefore turn on enterprise-only functionality on a deployment that
never installed the enterprise package. `features:enterpriseEdition` already solves exactly
this problem for itself, computed at runtime from package presence
(`src/codemie/configs/customer_config.py:154-174`) so YAML cannot override it. This story
extends that same mechanism to the other three flags.

## Approach

Add `costCenters`, `budgetManagement`, `projectChargeback` to `CONFIG_IDS`
(`src/codemie/configs/customer_config.py:30-49`, values `features:costCenters`,
`features:budgetManagement`, `features:projectChargeback` — matching today's YAML ids) and
give each a `_get_runtime_config()` entry (`customer_config.py:149-303`). Each entry's
`enabled` value is the AND of two parts, both computed as local values *before* any
`Component` is built for these keys — the same shape `enterpriseEdition` and
`metricsAnalytics` already use (they share one `is_enterprise = has_enterprise()` local,
computed once at `customer_config.py` L162 and reused unmodified at L164-169 and L272-277,
confirmed by direct read). Neither part may be computed by calling `is_feature_enabled`,
`is_component_enabled`, or `resolve_component` for these three keys: once the three keys
join `CONFIG_IDS`, those three functions route through `_get_runtime_config()` for them, so
calling any of them here would recurse into the method currently executing.

- The package-presence part reuses the existing `is_enterprise` local verbatim, as above —
  genuine reuse, zero new code.
- The YAML-declared part has no existing helper to reuse for these three keys. Two prior
  rounds of this spec cited `CustomerConfig.configured_feature_enabled()` for this read; a
  direct read this round found no such method exists — `customer_config.py:362-365` is the
  body of `get_assistant_model_override()`, and a repo-wide grep for
  `configured_feature_enabled` returns zero matches in `src/`. The real non-recursive
  primitive is the module-level `resolve()` function (`component_resolution.py:86-105`,
  already imported into this file under the alias `_resolve_component` at
  `customer_config.py:26`, confirmed): called with an empty `runtime_ids` set, it takes the
  YAML-plus-override branch only and never touches `_get_runtime_config()`. Treat its `None`
  result (absent component) as `False`, the same default `is_component_enabled()` already
  applies elsewhere. This is new code against an existing primitive, not reuse of a tested
  call path — give it its own unit tests. The exact call arguments (which instance attribute
  holds the parsed YAML component list) are confirmed at plan/implementation time, not
  asserted here.

`resolve_component` (`customer_config.py:436-445`) only consults `_get_runtime_config()` for
ids present in `CONFIG_IDS`, so membership there is the whole fix: every reader
(`is_feature_enabled`, `is_component_enabled`, `get_enabled_components`, the `GET /v1/config`
endpoint the UI reads) becomes package-aware with no other code change.

This automatically fixes `CostCenterService.ensure_feature_enabled()`
(`src/codemie/service/cost_center_service.py:62-68`) and `ProjectService`'s chargeback guard
(`src/codemie/service/project/project_service.py:277-284`), since both already call
`customer_config.is_feature_enabled(...)` directly — covering AC1, AC3, AC5, AC7 for those two
flags with zero service-layer change.

Budget Management has no equivalent existing guard to inherit the fix, and its real figures
are reachable through a third surface AC2 names: `CostCenterService`'s
`get_cost_center_budget_rollup` (`cost_centers.py:148-166`) calls
`get_cost_center_budget_summary` (`cost_center_budget_summary.py:252-285`), which returns
soft/max budget and current-spending figures with no feature-flag check of its own — gated
today only by `costCenters`, at the router that calls it — so disabling `budgetManagement`
alone, with `costCenters` left on, still exposes those figures. Introduce one shared guard — a
function mirroring `CostCenterService.ensure_feature_enabled()`'s `ExtendedHTTPException(403,
...)` shape, defined in a new module `src/codemie/service/budget/feature_gate.py` (a sibling
of `cost_center_budget_summary.py` in the same package, not defined inside any of its three
callers) — that checks `customer_config.is_feature_enabled("budgetManagement")`, wired as
follows:
- `project_budget_router.py` exports two independent `APIRouter` objects: `router` (prefix
  `/v1/admin/project-budgets`, `APIRouter(...)` at line 63, 10 handlers) and `group_router`
  (prefix `/v1/admin/project-budget-groups`, `APIRouter(...)` at line 452, 7 handlers covering
  project-budget-group create/list/get/update/delete/reset/rebalance), each registered
  independently via `app.include_router()` (`main.py:1493-1494`, confirmed by direct read).
  Add the guard as a router-level dependency (`dependencies=[Depends(...)]`) on **both**
  `APIRouter(...)` constructions, and on `budget_router.py`'s single `APIRouter(...)`, so every
  current and future route on all three router objects is covered by construction rather than
  by a per-handler call a future addition could omit. `project_budget_router.py`'s existing
  `_require_budgeting_enabled()` no-op (currently called manually at the top of all 17
  handlers, across both router objects) and `budget_router.py`'s separate
  `require_litellm_enabled()` (package/ops-toggle check, a different concern) both stay; the
  new dependency is additional, not a replacement for either.
- `get_cost_center_budget_summary()` itself — add the guard call inside the function, before
  it returns any figures, not only at its one current router caller — so a future second
  caller of this shared aggregation function cannot bypass the check by construction,
  matching `CostCenterService.ensure_feature_enabled()`'s own chokepoint-enforcement
  convention.

All three surfaces read the same `customer_config.is_feature_enabled("budgetManagement")`
result through the one guard, so there is a single source of truth for "is Budget Management
available," not checks that can drift apart.

UI: `src/pages/settings/tabs.tsx` reads each flag through `src/utils/featureFlags.ts`
wrappers over the same backend-reported component list, so Cost Centers and Chargeback need
no UI change — their flag-only reads become correct once the backend caps the flag. Budget
Management's nav entry is composed in two places, confirmed by direct read this round:
`budgetsManagementTab` itself (`tabs.tsx:162-172`) is built from the flag plus a role check
only, no enterprise check; the Admin branch of `buildAdministrationChildren()` then passes it
into `getEnterpriseAdminItems(ctx.isMcpFeatureEnabled, ctx.budgetsManagementTab)` behind
`ctx.isEnterprise ? ... : []` (`tabs.tsx:119-121`), which also gates Categories, MCP, and
Providers management behind that same `ctx.isEnterprise` check; the Auditor branch
(`tabs.tsx:124-128`) spreads `ctx.budgetsManagementTab` directly with no enterprise check at
all. The fix is narrower than a one-line deletion: move `budgetsManagementTab` out of the
Admin branch's `getEnterpriseAdminItems()` argument list so it is included unconditionally
(matching Cost Centers' pattern at `tabs.tsx:92-101`), while Categories, MCP, and Providers
management stay behind `ctx.isEnterprise` exactly as today. The route guard at
`router.tsx:601` already wraps the Budget Management route in the same `FeatureGuard`
mechanism Cost Centers' routes use (`router.tsx:578,587`); no route-guard change is needed.
Because `codemie` and `codemie-ui` deploy independently, the backend change must ship and be
verified live before the UI change goes out — a UI-first deploy would drop the only existing
protection on a non-enterprise deployment with `budgetManagement: true` in YAML before the new
backend ceiling exists to replace it, opening a zero-protection window. If both cannot land in
one release train, keep the Admin branch's `ctx.isEnterprise` composition around
`budgetsManagementTab` in place until the backend ceiling has been live for a release, then
remove it in a follow-up.

## Decisions

- Extended `CONFIG_IDS` + `_get_runtime_config()` for all three flags, over introducing a
  separate standalone AND-function per flag — both "competing precedents" the story named turn
  out to already live inside `_get_runtime_config()`; a new per-flag function would be a second
  way to do the same thing for no benefit.
- Computed each flag as (YAML-declared value) AND `is_enterprise`, over writing new
  special-case handling for the "absent from YAML" case — the non-recursive read for the
  YAML-declared part (the module-level `resolve()` function, `component_resolution.py:86-105`)
  returns `None` for an absent component, and `None` is treated as `False` for that part of the
  AND, the same default `is_component_enabled()` already applies elsewhere — so AC6 falls out
  of the same AND with no extra special-case code. (Supersedes citing
  `configured_feature_enabled()` for this read across two prior rounds: a direct read this
  round found no such method — `customer_config.py:362-365` is `get_assistant_model_override()`
  — so the YAML-declared part is new code against the `resolve()` primitive, not reuse of an
  existing call path.)
- No new UI affordance for "hidden because the package is missing" vs. "hidden because the flag
  is off" — settled by the story's own Non-goals; the UI composition fix in `tabs.tsx` is a
  consistency cleanup, not a visible-behavior change.
- Treated Budget Management as having zero server-side enforcement today, over assuming an
  enforcement point the story's prep missed — confirmed by direct read of
  `project_budget_router.py:223-225` (`_require_budgeting_enabled()` is a literal no-op stub).
- `budget_router.py` keeps `require_litellm_enabled()` and gains the new shared
  `budgetManagement` guard as a router-level dependency, over replacing `require_litellm_enabled()`
  or writing an independent inline check — package+ops-toggle and customer-facing flag are
  different concerns an operator can flip independently, and the shared guard keeps the
  customer-flag check itself in one place instead of three.
- Introduced one shared guard — defined in a new `src/codemie/service/budget/feature_gate.py`
  module, not inside `project_budget_router.py`, `budget_router.py`, or
  `cost_center_budget_summary.py` — reused by all three of those files, over three
  independently-written checks — matches the one existing convention the codebase already has
  for this exact purpose (`CostCenterService.ensure_feature_enabled()`) and removes the drift
  risk of three call sites evolving differently. Wired as a router-level dependency on both of
  `project_budget_router.py`'s `APIRouter` objects (`router` and `group_router`) and on
  `budget_router.py`'s `APIRouter` (not only a per-handler call), so a future route added to any
  of the three router objects is covered by construction; wired as a call inside
  `get_cost_center_budget_summary()` itself (not only at its router caller) for the Cost Centers
  budget-rollup path, so a future second caller cannot bypass it.
- The `budgetManagement` guard dependency is ordered **after** `authenticate` on every route it
  is added to (`Depends(authenticate)` resolved first, then the guard), over leaving the two
  unordered — an unauthenticated caller must get `401` before ever learning whether
  `budgetManagement` is enabled on this deployment, matching how every other authenticated
  endpoint in this codebase already behaves. This does not change AC2/AC5/AC7: an authenticated
  caller without the feature still gets rejected exactly as before, only the precedence relative
  to an *unauthenticated* caller is now explicit rather than left as an open question.
- Extended Budget Management's in-scope API surface to include the Cost Centers budget-rollup
  endpoint, over leaving it covered only by `costCenters` — it returns real soft/max budget and
  spending figures today gated only by `costCenters`, and AC2's own wording ("including where it
  isn't currently enforced server-side") reads as already deciding this gap should close rather
  than stand as a named exception.
- `tabs.tsx`'s Budget Management tab is moved out of the Admin branch's `isEnterpriseEdition()`-
  gated composition (not deleted outright, since that same composition also covers unrelated
  Categories/MCP/Providers management), over leaving the redundant check in place, so all three
  tabs follow one pattern instead of two.
- Backend ships and is verified live before `codemie-ui` removes Budget Management's
  `isEnterpriseEdition()` composition, over a simultaneous release train — a UI-first deploy
  would remove the only existing client-side protection before the new backend ceiling exists,
  opening a zero-protection window on non-enterprise deployments with `budgetManagement: true` in
  YAML.
- Accepted the added per-request cost of rebuilding `_get_runtime_config()` for these three
  now-hot-path flags, over caching the rebuild for the duration of a single request — the cost
  is bounded and currently zero by default (`MARKETPLACE_MANAGEMENT_ENABLED` defaults to `False`,
  the one condition that adds synchronous DB reads to the rebuild); request-scoped caching is a
  possible future optimization, not required for this story's correctness goal.

## Acceptance Criteria

1. Without `codemie-enterprise`, `costCenters: true` still leaves Cost Centers unavailable in API and UI.
2. Without `codemie-enterprise`, `budgetManagement: true` still leaves Budget Management
   unavailable in API (`budget_router.py`, `project_budget_router.py`, and the Cost Centers
   budget-rollup endpoint) and UI.
3. Without `codemie-enterprise`, `projectChargeback: true` still leaves Chargeback unavailable in API and UI.
4. With `codemie-enterprise` installed and all three flags `true`, behavior is unchanged from today.
5. Without `codemie-enterprise`, direct API calls to any of the three surfaces are rejected the
   same way as an explicitly disabled flag.
6. Without `codemie-enterprise`, a flag omitted from YAML defaults to unavailable.
7. With `codemie-enterprise` installed but a flag `false`, the feature stays disabled.

## Non-goals

- The standalone `codemie-chargeback` BigQuery aggregator (separate repo, no coupling to
  `customer_config` or `codemie_enterprise`).
- Changing default enablement when the enterprise package is installed.
- New UI explaining why a feature is hidden.
- Chargeback's functional dependency on Cost Centers (tracked separately).

## Testing

Model new `customer_config.py` tests on `test_runtime_features_enterprise_installed` /
`_not_installed` and `test_runtime_features_override_yaml`
(`tests/codemie/configs/test_customer_config.py`) for all three flags. Update
`test_is_feature_enabled_project_chargeback`, now that `projectChargeback` is runtime-computed.
Add a unit test for the new YAML-only, non-recursive read helper itself (absent, `false`, and
`true` cases). Add tests for the new shared `budgetManagement` guard covering all three call
sites it is wired into: `project_budget_router.py`'s `router` **and** `group_router` (e.g. a
403 from `list_project_budget_groups` with `budgetManagement` disabled, not only from a
`router` endpoint) and `budget_router.py`'s router-level dependency rejecting a request, and
`get_cost_center_budget_rollup`/`get_cost_center_budget_summary` rejecting (or omitting budget
figures) when `budgetManagement` is disabled while `costCenters` stays enabled. Add a test
asserting an unauthenticated request to a `budgetManagement`-guarded endpoint returns `401`, not
`403`, confirming the guard never runs before `authenticate`. UI:
update/add a `tabs.tsx` test asserting Budget Management's visibility no longer depends on
`isEnterpriseEdition()` for the Admin role, while Categories/MCP/Providers management still do.

## Open Risks

- This round's architecture review directly read `codemie-ui/src/pages/settings/tabs.tsx`
  (lines 1-180) and confirmed `router.tsx:578,587,601`; the Approach's UI citations above
  reflect that direct read. Other UI claims in this spec not covered by that read
  (`featureFlags.ts`/`FeatureGuard.tsx`'s exact internals, `ChargebackSettings.tsx`, the Cost
  Centers in-page conditionals) still rest on the technical analysis's facts layer rather than a
  direct read in this run — confirm at plan/implementation time before relying on their exact
  line numbers.
- On a deployment with `MARKETPLACE_MANAGEMENT_ENABLED=True`, every Cost Centers/Budget
  Management/Chargeback request now pays the cost of rebuilding the full runtime-component list,
  including up to four synchronous DB reads inside `is_marketplace_management_enabled()`'s config
  lookup. Accepted as bounded and off by default (see Decisions); revisit with request-scoped
  caching of `_get_runtime_config()` if this proves measurable in practice.
