# Enterprise Feature Gating by Package Presence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `costCenters`, `budgetManagement`, and `projectChargeback` runtime-computed (YAML flag AND `codemie-enterprise` package presence), mirroring `enterpriseEdition`, so none of the three can be enabled on a deployment without the package — backend (`codemie` repo) only.

**Architecture:** Add the three ids to `CustomerConfig.CONFIG_IDS` and give each a `_get_runtime_config()` entry computed as (YAML-declared value via a new non-recursive helper) AND the already-computed `is_enterprise` local — exactly `enterpriseEdition`/`metricsAnalytics`'s shape. This alone fixes every existing reader (`is_feature_enabled`, `GET /v1/config`, Cost Centers' and Chargeback's existing guards). Budget Management has no existing backend guard, so introduce one shared guard module and wire it as a router-level FastAPI dependency (with its own `Depends(authenticate)` sub-dependency, so 401 always precedes 403) onto `project_budget_router.py`'s two router objects and `budget_router.py`'s router, plus a direct call inside `get_cost_center_budget_summary()`'s one chokepoint.

**Tech Stack:** Python, FastAPI, pydantic, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-11-05-gate-enterprise-only-features/spec.md`

## Out of scope — separate run required

The spec's UI fix (`codemie-ui`'s `src/pages/settings/tabs.tsx`: move `budgetsManagementTab` out of the Admin branch's `getEnterpriseAdminItems()` argument list so it's included unconditionally, matching Cost Centers' pattern) lives in the `codemie-ui` repository and is **not** planned here. It needs its own planning/implementation run in that repo, and per the spec's own Decisions, must not ship before this backend change is live (a UI-first deploy would remove the only existing protection before the new backend ceiling exists).

## Global Constraints

- Never compute either half of the three flags' AND by calling `is_feature_enabled`, `is_component_enabled`, or `resolve_component` for these three keys inside `_get_runtime_config()` — once the keys join `CONFIG_IDS`, those three functions route through `_get_runtime_config()` for them, so calling any of them there recurses into the method currently executing.
- `project_budget_router.py`'s `_require_budgeting_enabled()` no-op and `budget_router.py`'s `require_litellm_enabled()` both stay exactly as they are — the new guard is additional, never a replacement for either.
- The new shared guard is a single module (`src/codemie/service/budget/feature_gate.py`), reused by all three call sites — not three independently-written checks.
- No new UI affordance anywhere in this change for "hidden because the package is missing" vs. "hidden because the flag is off" (non-goal).
- No request-scoped caching of `_get_runtime_config()` — the added rebuild cost is accepted as-is (spec Decisions).
- Commit per task using the repository's existing convention.

## Review Focus

- An unauthenticated caller hitting a `budgetManagement`-guarded route must get 401, never 403 — every sibling admin surface answers 401 first, and a bare router-level dependency would otherwise run before `authenticate` resolves (AR-12). Owned by Task 2 (guard's own `Depends(authenticate)`) and tested in Task 3.
- A flag omitted from YAML must resolve to unavailable, not accidentally `True` — covered by the new YAML-only helper's own absent/false/true unit tests (Task 1).
- An enterprise-installed deployment with a flag explicitly `true` must behave exactly as today (no regression on the already-shipped Cost Centers/Chargeback guards) — covered by the enterprise-installed runtime tests (Task 1).
- A future second caller of `get_cost_center_budget_summary()` must not be able to bypass the `budgetManagement` check by skipping a router-level guard — covered by placing the check inside the function itself, not only at its router caller (Task 5).
- `project_budget_router.py`'s `group_router` (7 endpoints, a separate `APIRouter` object from `router`) must not stay unguarded because the dependency was only added to `router` — covered by wiring and testing both objects explicitly (Task 3).

## Negative Constraints Pass

- "Neither part may be computed by calling `is_feature_enabled`/`is_component_enabled`/`resolve_component`" (spec Approach) — honored by Task 1's `_yaml_declared_enabled` helper, which calls the module-level `resolve()` directly; no task calls the three forbidden functions from inside `_get_runtime_config()`.
- "`_require_budgeting_enabled()` ... stays" / "`require_litellm_enabled()` ... stays" (spec Decisions) — Tasks 3 and 4 only add a router-level `dependencies=[...]` entry; neither task touches either existing function's body or call sites.
- "No new UI affordance for 'package missing' vs. 'flag off'" (spec Non-goals) — no task in this plan touches any UI code or adds a distinguishing field to `ComponentSetting`.
- "Changing default enablement when the enterprise package is installed" (spec Non-goals) — no task changes behavior for an enterprise-installed deployment; Task 1's tests pin the no-regression case explicitly.
- "The standalone `codemie-chargeback` BigQuery aggregator" (spec Non-goals) — no task touches it; none of the five tasks reference that service.
- "Chargeback's functional dependency on Cost Centers" (spec Non-goals, tracked separately) — no task adds a `projectChargeback`-AND-`costCenters` check; Task 1's AND-ordering comment only documents the convention for a *future* change, it does not implement one.
- Guard ordering: "ordered after `authenticate`... never runs before" (spec Decisions) — Task 2 gives the guard its own `Depends(authenticate)` sub-dependency rather than relying on router-level list position, which is the only construction that makes this deterministic (AR-12); Task 3 adds the explicit 401-not-403 test.

---

### Task 1: Runtime-computed gating for the three flags in `customer_config.py`

**Files:**
- Modify: `src/codemie/configs/customer_config.py:30-49` (`CONFIG_IDS`), `:149-303` (`_get_runtime_config`)
- Test: `tests/codemie/configs/test_customer_config.py` (`TestRuntimeFeatures`, lines ~301-560; `test_is_feature_enabled_project_chargeback`, lines 254-279)

**Interfaces:**
- Produces: `CONFIG_IDS["costCenters"] = "features:costCenters"`, `CONFIG_IDS["budgetManagement"] = "features:budgetManagement"`, `CONFIG_IDS["projectChargeback"] = "features:projectChargeback"`; a new private method `CustomerConfig._yaml_declared_enabled(self, component_id: str) -> bool`.
- Consumed by: Task 5 does **not** use `_yaml_declared_enabled` (it's private to this file) — it uses `customer_config.is_feature_enabled("budgetManagement")` via Task 2's guard, which is now safe because the key is no longer YAML-only.

**Test-first:** yes — new `TestRuntimeFeatures` cases asserting `costCenters`/`budgetManagement`/`projectChargeback` resolve disabled when `has_enterprise()` is `False` even though the YAML flag is `true` (AC1/AC2/AC3), plus a new `_yaml_declared_enabled` unit test (absent/false/true) — all fail before Step 3's implementation because the `CONFIG_IDS` keys and the helper don't exist yet.

- [ ] **Step 1: Write failing tests**

In `TestRuntimeFeatures`, add test methods modeled on `test_runtime_features_enterprise_installed`/`test_runtime_features_enterprise_not_installed` (same mock shape: `has_enterprise` patched, `Path.read_text` patched with a YAML fixture declaring `features:costCenters`/`features:budgetManagement`/`features:projectChargeback`), asserting:
- enterprise installed + flag `true` → component enabled
- enterprise installed + flag `false` → component disabled (AC7)
- enterprise installed + flag absent from YAML → component disabled (AC6)
- enterprise **not** installed + flag `true` → component disabled (AC1/AC2/AC3)

Also add a new `TestCustomerConfig` test for `_yaml_declared_enabled` directly: absent component → `False`; `enabled: false` → `False`; `enabled: true` → `True` (no `has_enterprise` mock needed — this helper never touches runtime computation).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/codemie/configs/test_customer_config.py -v`
Expected: FAIL — new tests fail (`KeyError`/`AttributeError`), since the three keys and the helper don't exist yet.

- [ ] **Step 3: Implement**

Add the three entries to `CONFIG_IDS` (`customer_config.py:30-49`), matching today's YAML ids verbatim (`features:costCenters`, `features:budgetManagement`, `features:projectChargeback`).

Add the helper near `_get_runtime_config` (e.g. directly above it):

```python
def _yaml_declared_enabled(self, component_id: str) -> bool:
    """YAML-plus-override value for one component, never touching runtime computation.

    Safe to call from inside `_get_runtime_config()` itself: unlike `is_feature_enabled`/
    `is_component_enabled`/`resolve_component`, this always passes an empty `runtime_ids`
    set, so it takes the YAML-plus-override branch only and cannot recurse into the
    method currently building the runtime list. An absent component resolves to `False`,
    the same default `is_component_enabled()` already applies elsewhere.
    """
    component = _resolve_component(component_id, self.components, [], set())
    return component is not None and component.settings.enabled
```

Inside `_get_runtime_config()`, after the existing `is_enterprise = has_enterprise()` (line ~162), append:

```python
# Cost Centers / Budget Management / Chargeback: the YAML flag alone raises nothing;
# `is_enterprise` (computed above) is the hard ceiling, mirroring enterpriseEdition's
# shape. A future cross-flag AND (e.g. projectChargeback AND costCenters) must read the
# costCenters Component already appended below off this in-progress `runtime_config`
# list, not re-derive it independently.
runtime_config.append(
    Component(
        id=CONFIG_IDS["costCenters"],
        settings=ComponentSetting(
            enabled=self._yaml_declared_enabled("features:costCenters") and is_enterprise
        ),
    )
)

runtime_config.append(
    Component(
        id=CONFIG_IDS["budgetManagement"],
        settings=ComponentSetting(
            enabled=self._yaml_declared_enabled("features:budgetManagement") and is_enterprise
        ),
    )
)

runtime_config.append(
    Component(
        id=CONFIG_IDS["projectChargeback"],
        settings=ComponentSetting(
            enabled=self._yaml_declared_enabled("features:projectChargeback") and is_enterprise
        ),
    )
)
```

(Placement within the method body is otherwise free — anywhere after `is_enterprise` is assigned.)

- [ ] **Step 4: Update the test the spec names as contradicted**

`test_is_feature_enabled_project_chargeback` (`test_customer_config.py:254-279`) currently asserts purely-YAML behavior with no `has_enterprise` mock. Split it (or add `has_enterprise` patches per case) so it asserts: enterprise installed + YAML `true` → `True`; enterprise installed + YAML `false`/absent → `False`; enterprise **not** installed + YAML `true` → `False` (the new case this story adds).

- [ ] **Step 5: Run full file, reconcile the four count assertions**

Run: `pytest tests/codemie/configs/test_customer_config.py -v`
`test_runtime_features_enterprise_installed`, `test_runtime_features_enterprise_not_installed`, `test_runtime_features_override_yaml`, and `test_disabled_runtime_features_excluded_like_yaml` each assert a literal `len(components)` from `get_enabled_components()`. None of their YAML fixtures declare the three new ids, so the new runtime entries resolve disabled (YAML-absent half of the AND is `False`) and stay excluded from the enabled list regardless of `is_enterprise` — confirm this by running rather than assuming; if any count differs from today's value, update that literal and its explanatory comment to the real post-change count and state why.
Expected: PASS, all tests in the file.

- [ ] **Step 6: Commit**

---

### Task 2: Shared `budgetManagement` guard module

**Files:**
- Create: `src/codemie/service/budget/feature_gate.py`
- Test: `tests/codemie/service/budget/test_feature_gate.py` (new)

**Interfaces:**
- Produces: `ensure_budget_management_enabled() -> None` (raises `ExtendedHTTPException(code=403, ...)` when `customer_config.is_feature_enabled("budgetManagement")` is `False`); `require_budget_management_enabled(user: User = Depends(authenticate)) -> None` (FastAPI dependency wrapper — declaring `Depends(authenticate)` as its own parameter forces FastAPI to resolve authentication, and therefore any 401, before this function's body runs, regardless of where it sits in a router's `dependencies=[]` list; AR-12's fix).
- Consumed by: Task 3 (`project_budget_router.py`), Task 4 (`budget_router.py`), Task 5 (`cost_center_budget_summary.py`, via `ensure_budget_management_enabled` directly — not the FastAPI wrapper, since that call site is not a route dependency).

**Test-first:** yes — `test_ensure_budget_management_enabled_raises_when_disabled` / `_passes_when_enabled`, both failing with `ModuleNotFoundError` before Step 3 creates the module.

- [ ] **Step 1: Write failing tests**

```python
def test_ensure_budget_management_enabled_raises_when_disabled():
    with patch("codemie.service.budget.feature_gate.customer_config.is_feature_enabled", return_value=False):
        with pytest.raises(ExtendedHTTPException) as exc:
            ensure_budget_management_enabled()
        assert exc.value.code == 403

def test_ensure_budget_management_enabled_passes_when_enabled():
    with patch("codemie.service.budget.feature_gate.customer_config.is_feature_enabled", return_value=True):
        ensure_budget_management_enabled()  # does not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/codemie/service/budget/test_feature_gate.py -v`
Expected: FAIL (`ModuleNotFoundError` — the module doesn't exist yet).

- [ ] **Step 3: Implement**

```python
from fastapi import Depends

from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User


def ensure_budget_management_enabled() -> None:
    """Raise 403 unless `features:budgetManagement` is available (flag AND enterprise ceiling).

    Call this from any code path that returns Budget Management data or performs a Budget
    Management action — including a future second caller not wired through the FastAPI
    dependency below — so the check can't be bypassed by construction.
    """
    if not customer_config.is_feature_enabled("budgetManagement"):
        raise ExtendedHTTPException(
            code=403,
            message="Feature not available",
            details="Budget management is not enabled for this customer.",
            help="Contact your system administrator to enable the 'features:budgetManagement' component.",
        )


def require_budget_management_enabled(user: User = Depends(authenticate)) -> None:
    """Router-level dependency. `authenticate` resolves first (401 before 403), always."""
    ensure_budget_management_enabled()
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/codemie/service/budget/test_feature_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 3: Wire the guard onto `project_budget_router.py` (`router` and `group_router`)

**Files:**
- Modify: `src/codemie/rest_api/routers/project_budget_router.py:63-67` (`router = APIRouter(..., dependencies=[])`), `:452-456` (`group_router = APIRouter(..., dependencies=[])`)
- Test: `tests/codemie/rest_api/routers/test_project_budget_router.py`

**Interfaces:**
- Consumes: `require_budget_management_enabled` from Task 2's `src/codemie/service/budget/feature_gate.py`.

**Test-first:** yes — `test_router_endpoint_rejects_when_budget_management_disabled`, `test_group_router_endpoint_rejects_when_budget_management_disabled`, and `test_router_endpoint_requires_auth_before_budget_management_check`, all failing before Step 3 because both routers' `dependencies=[]` are still empty.

- [ ] **Step 1: Write failing tests**

Using this file's existing `app.include_router(router)` / `TestClient` / `app.dependency_overrides[authenticate] = lambda: user` harness (see `test_project_budget_router.py:1059-1065` for the pattern):
- `test_router_endpoint_rejects_when_budget_management_disabled` — with `customer_config.is_feature_enabled` patched to return `False` for `"budgetManagement"`, call any `router` endpoint (e.g. `list_project_budgets`) as an authenticated user; assert `403`.
- `test_group_router_endpoint_rejects_when_budget_management_disabled` — same patch, call `group_router`'s `list_project_budget_groups`; assert `403` (closes AR-10: the group router is a separate `APIRouter` object and must be independently covered).
- `test_router_endpoint_requires_auth_before_budget_management_check` — same patch (`budgetManagement` disabled) but do **not** set `app.dependency_overrides[authenticate]`; call a `router` endpoint with no credentials; assert `401`, not `403` (the deferred AR-12 requirement).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/codemie/rest_api/routers/test_project_budget_router.py -v`
Expected: FAIL — no rejection happens yet (both `dependencies=[]` lists are empty), so the new assertions fail.

- [ ] **Step 3: Implement**

Import `require_budget_management_enabled` from `codemie.service.budget.feature_gate`. Change both `dependencies=[]` to `dependencies=[Depends(require_budget_management_enabled)]` — at the `router = APIRouter(...)` construction (line ~63) and the `group_router = APIRouter(...)` construction (line ~452).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/codemie/rest_api/routers/test_project_budget_router.py -v`
Expected: PASS, including every pre-existing test in the file (the dependency must not break an already-enabled-by-default test run — confirm `customer_config.is_feature_enabled` is not patched `False` anywhere it would now unexpectedly 403 an existing test).

- [ ] **Step 5: Commit**

---

### Task 4: Wire the guard onto `budget_router.py`

**Files:**
- Modify: `src/codemie/rest_api/routers/budget_router.py:42-46` (`router = APIRouter(..., dependencies=[])`)
- Test: `tests/codemie/rest_api/routers/test_budget_router.py`

**Interfaces:**
- Consumes: `require_budget_management_enabled` from Task 2's `src/codemie/service/budget/feature_gate.py`.

**Test-first:** yes — `test_create_budget_rejects_when_budget_management_disabled` and `test_endpoint_requires_auth_before_budget_management_check`, both failing before Step 3 because `router`'s `dependencies=[]` is still empty.

- [ ] **Step 1: Write failing tests**

- `test_create_budget_rejects_when_budget_management_disabled` — `customer_config.is_feature_enabled` patched `False` for `"budgetManagement"`; call `create_budget` (or any endpoint on this router) as an authenticated maintainer; assert `403`.
- `test_endpoint_requires_auth_before_budget_management_check` — same patch, no `authenticate` override; assert `401`, not `403`.
- Confirm (no new test needed, just read) that `require_litellm_enabled()`'s existing inline calls inside each handler body are untouched and still run — the new dependency is additional, not a replacement.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/codemie/rest_api/routers/test_budget_router.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Import `require_budget_management_enabled`. Change `router = APIRouter(..., dependencies=[])` to `dependencies=[Depends(require_budget_management_enabled)]`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/codemie/rest_api/routers/test_budget_router.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

---

### Task 5: Chokepoint guard inside `get_cost_center_budget_summary()`

**Files:**
- Modify: `src/codemie/service/budget/cost_center_budget_summary.py:252-265` (insert the guard call as the first statement after the empty-`project_names` early return, i.e. immediately before `spend_by_project, removed_by_project = await project_budget_spend(project_names)` at line ~265)
- Test: `tests/codemie/service/budget/test_cost_center_budget_summary.py`, `tests/codemie/rest_api/routers/test_cost_centers_router.py` (`get_cost_center_budget_rollup`)

**Interfaces:**
- Consumes: `ensure_budget_management_enabled` from Task 2's `src/codemie/service/budget/feature_gate.py` (the plain function, not the FastAPI-dependency wrapper — this call site is a direct function call inside an already-authenticated handler, not a route dependency).

**Test-first:** yes — `test_get_cost_center_budget_summary_rejects_when_budget_management_disabled` and `test_get_cost_center_budget_rollup_rejects_when_budget_management_disabled_but_cost_centers_enabled`, both failing before Step 3 because no check exists yet.

- [ ] **Step 1: Write failing tests**

- `test_get_cost_center_budget_summary_rejects_when_budget_management_disabled` — `customer_config.is_feature_enabled` patched `False` for `"budgetManagement"`; call `get_cost_center_budget_summary(["proj-1"])`; assert it raises `ExtendedHTTPException` with `code == 403`, and assert `project_budget_spend`/`get_distinct_member_counts_by_category` were never called (patch both, assert `not called` — proves the guard short-circuits before the DB work, not only before the return).
- `test_get_cost_center_budget_rollup_rejects_when_budget_management_disabled_but_cost_centers_enabled` — router-level test on `get_cost_center_budget_rollup`: `customer_config.is_feature_enabled` returns `True` for `"costCenters"` but `False` for `"budgetManagement"`; assert the endpoint responds `403` (proves `costCenters` alone is no longer sufficient, per AC2).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/codemie/service/budget/test_cost_center_budget_summary.py tests/codemie/rest_api/routers/test_cost_centers_router.py -v`
Expected: FAIL — no check exists yet, so `project_budget_spend` is called and no exception is raised.

- [ ] **Step 3: Implement**

Import `ensure_budget_management_enabled` from `codemie.service.budget.feature_gate`. Add the call as the first statement after the existing `if not project_names: return CostCenterBudgetSummary(budgets=[])` block, before the `project_budget_spend(project_names)` await.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/codemie/service/budget/test_cost_center_budget_summary.py tests/codemie/rest_api/routers/test_cost_centers_router.py -v`
Expected: PASS, including every pre-existing test in both files.

- [ ] **Step 5: Commit**

---

## Self-Review

**Spec coverage:** AC1/AC3/AC5 (Cost Centers, Chargeback) — fixed automatically by Task 1, since both already call `is_feature_enabled`. AC2 (Budget Management, all three surfaces) — Tasks 2-5. AC4/AC7 (enterprise-installed, flag true or false, no regression) — Task 1's enterprise-installed test cases. AC6 (YAML-absent defaults unavailable) — Task 1's helper and its absent-case test. The three spec-deferred items (test-count shift, AND-ordering comment, 401-vs-403) are each owned by a named task (1, 1, 2+3 respectively).

**Type/name consistency:** `require_budget_management_enabled` and `ensure_budget_management_enabled` (Task 2) are the exact names Tasks 3/4/5 import. `CONFIG_IDS["budgetManagement"]`/`"costCenters"`/`"projectChargeback"` (Task 1) are the exact keys `customer_config.is_feature_enabled("budgetManagement")` (Task 2) resolves through — no other task introduces a competing name for the same concept.

**Proportion:** five tasks, each a single cohesive, independently-testable change; no task's code block reproduces an existing file's surrounding code, only genuinely new symbols (the helper method, the new guard module).
