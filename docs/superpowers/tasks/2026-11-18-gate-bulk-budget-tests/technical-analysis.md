# Technical Research

**Task**: project budget router bulk tests budgetManagement CustomerConfig is_feature_enabled send_bulk_request send_member_request spend attribution drift test importorskip codemie_enterprise
**Generated**: 2026-11-18
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-17403 (Bug, Major) "Tests fail without codemie-enterprise: bulk budget router tests and test_attribution are not gated". Five bulk project-budget router tests (test_bulk_http_returns_results_and_budgets; four cases of test_bulk_422_for_zero_items_over_1000_and_negative_amounts) in tests/codemie/rest_api/routers/test_project_budget_router.py fail with 403 instead of 200/422 without codemie-enterprise, because the send_bulk_request fixture (added in EPMCDME-16071) does not pin the budgetManagement feature flag, unlike send_member_request (EPMCDME-14536). test_the_mirrored_project_composer_matches_its_source in tests/codemie/service/spend/test_attribution.py (EPMCDME-13429) imports codemie_enterprise.litellm directly and raises ModuleNotFoundError without the package. Proposed fix: pin budgetManagement on in send_bulk_request via patch.object(CustomerConfig, "is_feature_enabled", return_value=True) as in send_member_request; add pytest.importorskip("codemie_enterprise.litellm.service") to the drift test; keep test_router_endpoint_rejects_when_budget_management_disabled. Not covered by EPMCDME-17212 (conftest auto-skip wouldn't fix the 403). Acceptance: 1) without codemie-enterprise the five bulk tests pass and drift test skipped, no failure from either file under pytest tests/ --skip-enterprise; 2) with codemie-enterprise all 157 tests in the two files pass; 3) disabled-guard test still verifies the guard. Test-only change, no runtime/UI change.

---

## 2. Codebase Findings

### Existing Implementations
**Purpose: pin the budgetManagement flag for HTTP tests through the real router**
- `send_member_request` fixture, `tests/codemie/rest_api/routers/test_project_budget_router.py:1061-1079`. Builds a FastAPI app with `router` and the real `extended_http_exception_handler`. Inside `send`, it wraps the request in `with patch.object(CustomerConfig, "is_feature_enabled", return_value=True):`. Its docstring cites EPMCDME-14536.
- `send_bulk_request` fixture, same file, lines 1359-1370. It has the same app setup but no patch. `send(body, user=None)` sets `dependency_overrides[authenticate]` and calls `client.post(_BULK_PATH, json=body)`. `_BULK_PATH` is `/v1/admin/project-budgets/member-overrides/bulk` (line 1173).
- Consumers of `send_bulk_request` (the five failing cases):
  - `test_bulk_http_returns_results_and_budgets` (line 1379).
  - `test_bulk_422_for_zero_items_over_1000_and_negative_amounts` (line 1401), 4 parametrized ids: zero-items, over-1000-items, negative-max, negative-soft.
  - Both wrap the call in `_patch_bulk(...)`.
- Gate: `src/codemie/service/budget/feature_gate.py:33-63`. `is_budget_management_enabled` calls `customer_config.is_feature_enabled("budgetManagement")`. `ensure_budget_management_enabled` raises 403 "Feature not available". `budget_management_guard = [Depends(authenticate), Depends(require_budget_management_enabled)]`. The guard runs before request-body validation, which is why even the 422 cases return 403.
- `CustomerConfig.is_feature_enabled`, `src/codemie/configs/customer_config.py:552`. It reads `features:<key>` through `is_component_enabled`, which defaults to False when the component is absent. The test patches the class method, so the patch is process-wide.

**Purpose: skip tests that need codemie_enterprise**
- `tests/conftest.py:53-74`. The `--skip-enterprise` option adds a skip marker to items with the `enterprise` marker. Every test under `tests/enterprise/` is marked automatically. Unmarked tests elsewhere are not skipped.
- Existing `pytest.importorskip("codemie_enterprise...")` usage:
  - `tests/enterprise/litellm/test_unused_project_member_budget.py:46` (inside a function, with the same `codemie_enterprise.litellm.service` target).
  - `tests/codemie/rest_api/security/jwks/test_integration.py:32` (module level, with `reason=`).
  - `tests/codemie/rest_api/routers/test_user_router.py:1014` (in-function).
  - `tests/codemie/service/oauth/test_token_port.py:22`.
  - `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py:1092,1844` (in-function).

**Other touched code**
- `tests/codemie/service/spend/test_attribution.py:79-91`. `TestGrammar...` class, method `test_the_mirrored_project_composer_matches_its_source`. It does `from codemie_enterprise.litellm.service import LiteLLMService` inside the body (line 85). It compares `LiteLLMService._project_customer_id` with the in-core mirror `project_customer_id` for two vectors. Line 31 imports `codemie.enterprise.litellm.budget_categories` (the core-side module), not the enterprise package, so module import works without enterprise.

### Decisions Already in the Code
- Feature-flag pinning in tests is done by `patch.object(CustomerConfig, "is_feature_enabled", return_value=True)` around the request call, not around fixture setup (`send_member_request:1076`).
- The disabled path is covered with `return_value=False` in `test_router_endpoint_rejects_when_budget_management_disabled` (line 1138) and `test_group_router_endpoint_rejects_when_budget_management_disabled` (line 1148). Both use the separate `budget_management_app` fixture, not `send_bulk_request`.
- Enterprise gating in non-enterprise directories uses in-function `importorskip` (several precedents above). The alternative, the `enterprise` marker plus `--skip-enterprise`, applies only to marked tests or to tests under `tests/enterprise/`.

### Task Statements Checked Against the Code
- "send_bulk_request does not pin budgetManagement, unlike send_member_request": holds (lines 1359-1370 vs 1074-1077).
- "five bulk tests": holds (1 plus 4 parametrized ids).
- "drift test imports codemie_enterprise.litellm directly": holds (`test_attribution.py:85`, in-function import).
- "conftest auto-skip wouldn't fix the 403": holds. The skip applies only to the `enterprise` marker, and neither file is under `tests/enterprise/`. The 403 comes from the unset flag, not from a missing import.
- "157 tests in the two files": unverified, because tests were not run in this research.
- "test_router_endpoint_rejects_when_budget_management_disabled is kept": it exists at line 1138 and uses its own fixture, so it is independent of `send_bulk_request`.

### Consumers of Changed Contracts
- `send_bulk_request` is used only by the two bulk test functions (grep found only lines 1379-1405). Its signature `send(body, user=None)` is unchanged by the proposed fix.
- `CustomerConfig.is_feature_enabled` is patched at class level for the call duration only. It affects the whole process inside the `with` block, so any other `is_feature_enabled` call made during the request also returns True (e.g. `projectChargeback`, `costCenters`). None found in the bulk path from the explored code. This is unverified.

### Architecture and Layers Affected
Test layer only: REST router tests (`tests/codemie/rest_api/routers`) and spend service tests (`tests/codemie/service/spend`). The code under test, `feature_gate.py` and the project budget router, is untouched.

### Integration Points
- The router-level guard `budget_management_guard` (`feature_gate.py:63`), the `authenticate` dependency override, and the `_patch_bulk` helper.
- The optional extra `codemie_enterprise`, an external package.

### Patterns and Conventions
Fixtures build a `FastAPI` app, mount the router, register `extended_http_exception_handler`, and override `authenticate`. Flag pinning uses `patch.object` on `CustomerConfig`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/testing/testing-patterns.md` and `.ai-run/guides/testing/testing-api-patterns.md` are the relevant guides listed in AGENTS.md. They were not read in this pass. Conventions here are derived from code.

### Architectural Decisions
Fixture docstring at `test_project_budget_router.py:1063-1067` records the EPMCDME-14536 reason for pinning. The drift test docstring records its link to the enterprise composer (`codemie_enterprise/litellm/service.py:440`).

### Derived Conventions
`importorskip` with a dotted submodule is the repo's way to gate a single test or module on the enterprise package.

---

## 4. Testing Landscape

### Existing Coverage
`test_project_budget_router.py` covers member override endpoints, the guard (lines 1138, 1148) and bulk HTTP. `test_attribution.py` covers the grammar round trips and the mirrored composer.

### Testing Framework and Patterns
pytest with `unittest.mock.patch`, `AsyncMock` and `fastapi.testclient.TestClient`. The `--skip-enterprise` option is defined in `tests/conftest.py`.

### Coverage Gaps
None added by this task. After the fix, the drift guard is not exercised in environments without enterprise (by design).

---

## 5. Configuration and Environment

### Environment Variables
None found relevant. The flag is read from customer config component `features:budgetManagement`.

### Configuration Files
`src/codemie/configs/customer_config.py` resolves components (flag plus enterprise ceiling, per the `feature_gate.py` docstring).

### Feature Flags and Deployment Concerns
`features:budgetManagement` is the only flag involved. The tests ignore the real config by patching.

---

## 6. Risk Indicators

- The fix is small and test-only, in two files. Both failure causes are visible in the code.
- `patch.object` on `CustomerConfig` is process-wide while active. It would also turn on any other feature check inside the request (see Consumers).
- Speculative: the `importorskip` call can sit inside the test body, like `test_user_router.py:1014`. A module-level call would skip the whole `test_attribution.py` file, including tests that run without enterprise.
- Speculative: the 157-test count was not verified. Acceptance 2 needs enterprise installed to run.
- No run of the suite was made, so "fails without enterprise" is taken from the ticket.

---

## 7. Summary for Complexity Assessment

The change touches the test layer only, in two files, `tests/codemie/rest_api/routers/test_project_budget_router.py` and `tests/codemie/service/spend/test_attribution.py`. No source or runtime code is involved. The surface is one fixture (`send_bulk_request`, lines 1359-1370) and one test method (`test_attribution.py:79`).

There is no technical novelty. A precedent exists for each half: `send_member_request` pins the flag in the same file, and `importorskip("codemie_enterprise.*")` is used in six other test files. The root causes are confirmed in the code. The router guard runs before body validation, so the 422 cases also return 403, and the drift test imports the enterprise package inside its body.

Test coverage is already in place, including the disabled-guard tests, which use a separate fixture and are not affected. The main risks are the process-wide `patch.object` scope and the unverified test counts.

---

## 8. External References

None named by the task.
