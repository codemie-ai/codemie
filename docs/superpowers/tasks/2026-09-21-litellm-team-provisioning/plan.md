# LiteLLM Team Provisioning at Startup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision the `codemie-projects` LiteLLM team eagerly at startup by calling `litellm_service.ensure_team_exists()`, fail-open, before budget-provider registration.

**Architecture:** One new call site inside `_initialize_enterprise_services` in `src/codemie/rest_api/main.py`, reusing the already-constructed `litellm_service` local. No new module, no new config flag, no other layer touched.

**Tech Stack:** Python, FastAPI, pytest, `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-09-21-litellm-team-provisioning/spec.md`

## Global Constraints

- Gate: `litellm_service is not None and config.LLM_PROXY_BUDGET_CHECK_ENABLED` — no new flag introduced.
- Call must sit immediately **before** the budget-provider registration block (`main.py:303-308`), not after.
- Fail-open: `try/except Exception`, log a warning only; must never raise out of `_initialize_enterprise_services` and must never block the budget-provider registration block that follows.
- Reuse the local `litellm_service` variable from `main.py:292` — do not call `get_global_litellm_service()` or the enterprise loader.
- Do not modify `ensure_team_exists()`, `_team_exists()`, `_create_team_idempotent()`, or `_generate_project_key()` — all live in codemie-enterprise, out of scope.
- No new config flag, no deployment/Helm/runbook changes, no router/API/persistence/UI changes.
- Commit per task using the repository's existing convention.

---

### Task 1: Add eager `ensure_team_exists()` call site with fail-open guard

**Files:**
- Modify: `src/codemie/rest_api/main.py:296-308` (`_initialize_enterprise_services`)
- Test: `tests/codemie/rest_api/test_main_enterprise_services.py` (new)

**Interfaces:**
- Consumes: local `litellm_service` (already `initialize_litellm_from_config()` result at `main.py:292`), `config.LLM_PROXY_BUDGET_CHECK_ENABLED`, module-level `logger` (`codemie.configs.logger`).
- Produces: no new public symbol — behavior change only inside `_initialize_enterprise_services`.

Test-first: yes — new test module asserting `ensure_team_exists()` is called before budget-provider registration when both gate conditions are true, is skipped when either is false, and that an exception from it is swallowed with a warning while budget-provider registration still proceeds.

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/rest_api/test_main_enterprise_services.py` (license header per repo convention, mirroring `tests/codemie/rest_api/test_main_spend_tracking_setup.py`'s `patch("codemie.rest_api.main.config")` style):

```python
from unittest.mock import MagicMock, patch

import pytest


def _run(mock_litellm_service, budget_check_enabled):
    from codemie.rest_api.main import _initialize_enterprise_services

    app = MagicMock()
    with (
        patch("codemie.rest_api.main.config") as mock_config,
        patch("codemie.rest_api.main.get_observability_provider") as mock_get_obs,
        patch("codemie.rest_api.main.initialize_litellm_from_config", return_value=mock_litellm_service),
        patch("codemie.rest_api.main.set_global_litellm_service"),
        patch("codemie.enterprise.litellm.llm_proxy_provider_adapter.LiteLLMLLMProxyProvider"),
        patch("codemie.service.llm_proxy.provider_registry.register_llm_proxy_provider"),
        patch("codemie.enterprise.litellm.budget_provider_adapter.LiteLLMBudgetEnforcementProvider") as mock_budget_provider_cls,
        patch("codemie.service.budget.provider_registry.register_budget_enforcement_provider") as mock_register_budget,
    ):
        mock_config.LLM_PROXY_BUDGET_CHECK_ENABLED = budget_check_enabled
        mock_get_obs.return_value = MagicMock()
        _initialize_enterprise_services(app)
    return mock_budget_provider_cls, mock_register_budget


def test_ensure_team_exists_called_before_budget_provider_registration_when_gated():
    mock_service = MagicMock()
    call_order = []
    mock_service.ensure_team_exists.side_effect = lambda: call_order.append("team")
    with patch(
        "codemie.service.budget.provider_registry.register_budget_enforcement_provider",
        side_effect=lambda *_: call_order.append("budget"),
    ):
        _run(mock_service, budget_check_enabled=True)
    mock_service.ensure_team_exists.assert_called_once()
    assert call_order == ["team", "budget"]


def test_ensure_team_exists_skipped_when_budget_check_disabled():
    mock_service = MagicMock()
    _run(mock_service, budget_check_enabled=False)
    mock_service.ensure_team_exists.assert_not_called()


def test_ensure_team_exists_skipped_when_litellm_service_is_none():
    _run(None, budget_check_enabled=True)
    # no service to assert on; absence of AttributeError/None-call is the assertion


def test_ensure_team_exists_failure_is_logged_and_does_not_block_budget_registration():
    mock_service = MagicMock()
    mock_service.ensure_team_exists.side_effect = RuntimeError("litellm unreachable")
    with patch("codemie.rest_api.main.logger") as mock_logger:
        _, mock_register_budget = _run(mock_service, budget_check_enabled=True)
    mock_logger.warning.assert_called_once()
    mock_register_budget.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/test_main_enterprise_services.py -v`
Expected: FAIL — `ensure_team_exists` is never called because the production code doesn't call it yet (`assert_called_once()` / `call_order` assertions fail).

- [ ] **Step 3: Implement the call site**

In `src/codemie/rest_api/main.py`, insert a new block between line 294 (`set_global_litellm_service(litellm_service)`) and the existing `if litellm_service is not None:` proxy-provider block at line 296, so it runs before both existing blocks and directly ahead of the budget-provider block:

```python
    if litellm_service is not None and config.LLM_PROXY_BUDGET_CHECK_ENABLED:
        try:
            litellm_service.ensure_team_exists()
            logger.info("LiteLLM codemie-projects team provisioning ensured")
        except Exception as e:
            logger.warning(f"Failed to ensure LiteLLM codemie-projects team exists: {e}")
```

Place this new block immediately before the existing `if litellm_service is not None and config.LLM_PROXY_BUDGET_CHECK_ENABLED:` budget-provider-registration block (current `main.py:303-308`), so provisioning always precedes registration under the same gate. Leave the proxy-provider block (`main.py:296-301`) and the budget-provider block's body untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/test_main_enterprise_services.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 5 (local sanity check, optional — not a substitute for the pipeline's qa-gates stage):**

If validating against the real sibling implementation locally: `poetry run pip install -e ../codemie-enterprise`, then re-run the same test command and, if desired, boot the app locally with `LLM_PROXY_BUDGET_CHECK_ENABLED=true` and `INSTALL_ENTERPRISE=true` to confirm `ensure_team_exists()` resolves and executes without raising during real startup.

- [ ] **Step 6: Commit**

Commit per task using the repository's existing convention.

---

## Self-Review

**Spec coverage:**
- AC1 (call happens, gated, before budget-provider block) — Task 1, Step 3 + `test_ensure_team_exists_called_before_budget_provider_registration_when_gated`.
- AC2 (idempotent, never raises, doesn't depend on prior team state) — delegated to `ensure_team_exists()` itself per spec; this repo's contribution is the fail-open wrapper, covered by `test_ensure_team_exists_failure_is_logged_and_does_not_block_budget_registration`.
- AC3 (exception caught, warning logged, budget-provider block still runs) — same test.
- AC4 (skipped when `litellm_service is None` or flag false) — `test_ensure_team_exists_skipped_when_budget_check_disabled` and `test_ensure_team_exists_skipped_when_litellm_service_is_none`.

**negative-constraints:**
- No modification to `ensure_team_exists()` / `_team_exists()` / `_create_team_idempotent()` / `_generate_project_key()` — Task 1 only adds a call site in `main.py`; none of those symbols are touched or redefined.
- No new config flag — Task 1 reuses `config.LLM_PROXY_BUDGET_CHECK_ENABLED` exclusively; no flag is added to any config module.
- No deployment/Helm/Terraform/runbook changes — plan touches only `main.py` and one new test file.
- No router/API/persistence/UI changes — confirmed, only the startup function body changes.
- Local codemie-enterprise install (Step 5) is explicitly marked optional/local-only and not a stand-in for the pipeline's qa-gates stage.

**Placeholder scan:** no TBD/TODO, all steps contain concrete code or commands.

**Type consistency:** single task, single new symbol-free call site — no cross-task signature drift possible.
