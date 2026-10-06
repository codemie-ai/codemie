# Model Provider Startup Handling Implementation Plan

> **Status: implemented.** The plan below is preserved as originally written. The implementation deviated from it in several places — see [Deviations from this plan](#deviations-from-this-plan) at the end of this file. Where the two disagree, the source code is authoritative.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make model-provider readiness a startup-time fact: compute once at boot whether a hosted model provider (Azure OpenAI or AWS Bedrock, or LiteLLM proxy) is configured, log it, store it, and surface it on `GET /healthcheck` — without ever blocking startup and without changing the chat path when a provider is configured.

**Architecture:** A new pure-function module `codemie/src/codemie/service/model_provider_readiness.py` (mirrors `codemie/src/codemie/service/oauth_security.py`) computes a `ModelProviderReadiness` result from existing config/LLM-profile data — no new outbound calls, no new config plumbing. `main.py`'s `lifespan()` calls it once via a new small `_check_model_provider_readiness(app)` helper (same shape as the file's other `_initialize_*` helpers) and stores the result on `app.state`. `common.py`'s `healthcheck()` reads that stored value and adds a `model_provider` field to its existing response dict.

**Tech Stack:** Python, FastAPI, pydantic (via existing `LLMModel`/`LLMConfig`), boto3 (already a dependency), pytest + `monkeypatch`/`caplog` (existing test conventions).

**Spec:** `docs/superpowers/tasks/2026-09-07-epmcdme-14653-model-provider-startup-handling/technical-analysis.md` (sdlc-light has no separate spec.md — this plan argues directly from the ticket text embedded in that file's Section 1, plus its codebase findings).

## Global Constraints

- Never log or return secret *values* — only setting *names* (ticket requirement, and existing `oauth_security.py` convention).
- `/healthcheck` HTTP status code stays 200 in both configured and not-configured cases — do not touch its status code (out of scope).
- No outbound network call to Azure or AWS at startup — configuration/credential-chain *presence* only. `boto3.Session().get_credentials()` is a local resolution (env vars / shared credentials file / IAM role lookup), not a call to a provider API — consistent with the existing `get_bedrock_runtime_client()` precedent at `codemie/src/codemie/core/dependecies.py:168-189`.
- The check must not attempt to load or re-parse `config/llms/llm-<MODELS_ENV>-config.yaml` — that already happened at import time via the `llm_config` singleton (`codemie/src/codemie/configs/llm_config.py:147`); a load failure there already crashes the process before `lifespan()` runs, and changing that is out of scope.
- Do not touch the pre-existing uncommitted `Makefile` change on this branch (`CONTAINER_RUNTIME` support) — it is unrelated, already staged by the user.

---

### Task 1: `ModelProviderReadiness` result type + native-provider branch (Azure / Bedrock / other / no-default-model)

**Files:**
- Create: `codemie/src/codemie/service/model_provider_readiness.py`
- Test: `codemie/tests/codemie/service/test_model_provider_readiness.py`

**Interfaces:**
- Produces:
  - `class ModelProviderReadiness(NamedTuple)`: `configured: bool`, `provider: Optional[str]`, `missing: list[str]`
  - `def check_model_provider_readiness() -> ModelProviderReadiness`
  - Module-level imports other tasks will monkeypatch: `config` (from `codemie.configs`), `llm_service` (from `codemie.service.llm_service.llm_service`), `is_litellm_enabled` (from `codemie.enterprise.litellm` — same source `main.py` already imports it from; reused directly rather than re-deriving the `LLM_PROXY_ENABLED`/`LLM_PROXY_MODE` condition, so LiteLLM-proxy detection stays a single source of truth with the code that actually initializes the proxy service)

- [ ] **Step 1: Write the failing test for "no global default model"**

```python
# codemie/tests/codemie/service/test_model_provider_readiness.py
"""Model-provider startup readiness must name what's missing, and must never block startup."""

import codemie.service.model_provider_readiness as readiness_module
from codemie.configs.llm_config import LLMModel, LLMProvider


def _model(provider):
    return LLMModel(base_name="m", deployment_name="m", enabled=True, provider=provider)


def test_no_global_default_model_reports_not_configured(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service, "get_default_model_for_category", lambda category: None
    )

    result = readiness_module.check_model_provider_readiness()

    assert result.configured is False
    assert result.provider is None
    assert "MODELS_ENV" in result.missing[0]
```

Note: `monkeypatch.setattr(readiness_module, "is_litellm_enabled", ...)` requires `is_litellm_enabled` to already be an attribute of the module (imported by name, not called through a longer path) — Step 3 below imports it that way for exactly this reason.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.service.model_provider_readiness'`

- [ ] **Step 3: Write minimal implementation to pass Step 1**

```python
# codemie/src/codemie/service/model_provider_readiness.py
"""Startup readiness check for the configured model provider.

Predicts whether the first chat message will succeed, without making any outbound
call to a provider — configuration and local credential-chain presence only. Mirrors
the shape of codemie.service.oauth_security: pure functions, no framework, never logs
secret values (only setting names).
"""

from typing import NamedTuple, Optional

from codemie.configs import config, logger
from codemie.configs.llm_config import LLMProvider, ModelCategory
from codemie.enterprise.litellm import is_litellm_enabled
from codemie.service.llm_service.llm_service import llm_service


class ModelProviderReadiness(NamedTuple):
    configured: bool
    provider: Optional[str]
    missing: list[str]


def check_model_provider_readiness() -> ModelProviderReadiness:
    """Predict whether a chat request will succeed, from configuration alone."""
    if is_litellm_enabled():
        return ModelProviderReadiness(configured=True, provider="litellm_proxy", missing=[])

    default_model = llm_service.get_default_model_for_category(ModelCategory.GLOBAL)
    if default_model is None:
        return ModelProviderReadiness(
            configured=False,
            provider=None,
            missing=[f"no enabled global default model for MODELS_ENV={config.MODELS_ENV}"],
        )

    return ModelProviderReadiness(configured=True, provider=None, missing=[])
```

`is_litellm_enabled()` (`codemie/src/codemie/enterprise/litellm/dependencies.py:44-70`) is the same function `main.py` already uses to decide whether to call `initialize_litellm_from_config()` and register `app.state.litellm_service` — reusing it here keeps "is the LiteLLM proxy active" a single source of truth instead of re-deriving a parallel `LLM_PROXY_ENABLED`/`LLM_PROXY_MODE` condition that could drift from the real gating logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for the LiteLLM proxy branch**

```python
def test_litellm_proxy_mode_reports_configured_without_inspecting_credentials(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: True)

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        configured=True, provider="litellm_proxy", missing=[]
    )
```

- [ ] **Step 6: Run test to verify it passes (implementation from Step 3 already covers this)**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS (both tests)

- [ ] **Step 7: Write the failing tests for Azure OpenAI (configured, missing key, missing url)**

```python
def test_azure_configured(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AZURE_OPENAI),
    )
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_API_KEY", "key", raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "https://example", raising=False)

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        configured=True, provider="azure_openai", missing=[]
    )


def test_azure_missing_api_key(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AZURE_OPENAI),
    )
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "https://example", raising=False)

    result = readiness_module.check_model_provider_readiness()

    assert result.configured is False
    assert result.provider == "azure_openai"
    assert result.missing == ["AZURE_OPENAI_API_KEY"]


def test_azure_missing_url(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AZURE_OPENAI),
    )
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_API_KEY", "key", raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "", raising=False)

    result = readiness_module.check_model_provider_readiness()

    assert result.configured is False
    assert result.provider == "azure_openai"
    assert result.missing == ["AZURE_OPENAI_URL"]
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: `test_azure_configured` and the two missing-setting tests FAIL (Azure branch not implemented yet — default model with `provider=AZURE_OPENAI` currently falls through to the generic `ModelProviderReadiness(configured=True, provider=None, missing=[])` return, so `provider` and `missing` assertions fail).

- [ ] **Step 9: Extend implementation to add the Azure OpenAI branch**

Replace the final `return ModelProviderReadiness(configured=True, provider=None, missing=[])` line in `check_model_provider_readiness()` with:

```python
    provider = default_model.provider

    if provider == LLMProvider.AZURE_OPENAI:
        missing = []
        if not config.AZURE_OPENAI_API_KEY:
            missing.append("AZURE_OPENAI_API_KEY")
        if not config.AZURE_OPENAI_URL:
            missing.append("AZURE_OPENAI_URL")
        return ModelProviderReadiness(
            configured=not missing, provider=provider.value, missing=missing
        )

    return ModelProviderReadiness(configured=True, provider=None, missing=[])
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 11: Write the failing tests for the "other provider" and "unset provider" cases**

```python
def test_other_provider_reported_configured_without_credential_enforcement(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.GOOGLE_VERTEX_AI),
    )

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        configured=True, provider="google_vertexai", missing=[]
    )


def test_unset_provider_reports_not_configured(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service, "get_default_model_for_category", lambda category: _model(None)
    )

    result = readiness_module.check_model_provider_readiness()

    assert result.configured is False
    assert result.provider is None
    assert "m" in result.missing[0]  # names the model whose provider is unset
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: `test_other_provider_reported_configured_without_credential_enforcement` FAILS (`provider` comes back `None` instead of `"google_vertexai"`); `test_unset_provider_reports_not_configured` FAILS (`configured` comes back `True` instead of `False`).

- [ ] **Step 13: Replace the final fallback branch to handle "other provider" vs "unset provider"**

Replace the trailing `return ModelProviderReadiness(configured=True, provider=None, missing=[])` (now after the Azure block) with:

```python
    if provider is None:
        return ModelProviderReadiness(
            configured=False,
            provider=None,
            missing=[f"model provider unset for default model '{default_model.base_name}'"],
        )

    return ModelProviderReadiness(configured=True, provider=provider.value, missing=[])
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS (all tests so far — 7 tests)

- [ ] **Step 15: Commit**

```bash
git add codemie/src/codemie/service/model_provider_readiness.py codemie/tests/codemie/service/test_model_provider_readiness.py
git commit -m "EPMCDME-14653: Add model-provider readiness check (proxy, Azure, other-provider branches)"
```

---

### Task 2: AWS Bedrock branch — explicit, independently-testable readiness validation

**Files:**
- Modify: `codemie/src/codemie/service/model_provider_readiness.py`
- Test: `codemie/tests/codemie/service/test_model_provider_readiness.py`

**Interfaces:**
- Consumes: `ModelProviderReadiness`, `check_model_provider_readiness` from Task 1 (same file, extending the `provider ==` branch chain before the Task 1 fallback).
- Produces:
  - `def _has_resolvable_aws_credentials() -> bool` (module-private, monkeypatched directly by readiness tests; unit-tested once here against real `boto3.Session`).
  - `def _check_aws_bedrock_configured() -> ModelProviderReadiness` (module-private) — **explicit Bedrock readiness validation**, factored out of `check_model_provider_readiness()` into its own named, directly-testable function (mirrors why `_has_resolvable_aws_credentials` is its own function rather than inlined) rather than an anonymous inline `if provider == LLMProvider.AWS_BEDROCK:` block. `check_model_provider_readiness()` calls it once the selected profile's default model resolves to `LLMProvider.AWS_BEDROCK`; it in turn calls `_has_resolvable_aws_credentials()`.

- [ ] **Step 1: Write the failing unit test for `_has_resolvable_aws_credentials`**

```python
from unittest.mock import MagicMock, patch


def test_has_resolvable_aws_credentials_true_when_boto3_session_resolves_credentials():
    with patch("codemie.service.model_provider_readiness.boto3") as mock_boto3:
        mock_boto3.Session.return_value.get_credentials.return_value = MagicMock()
        assert readiness_module._has_resolvable_aws_credentials() is True


def test_has_resolvable_aws_credentials_false_when_boto3_session_has_no_credentials():
    with patch("codemie.service.model_provider_readiness.boto3") as mock_boto3:
        mock_boto3.Session.return_value.get_credentials.return_value = None
        assert readiness_module._has_resolvable_aws_credentials() is False


def test_has_resolvable_aws_credentials_false_when_boto3_session_raises():
    with patch("codemie.service.model_provider_readiness.boto3") as mock_boto3:
        mock_boto3.Session.side_effect = Exception("no config file")
        assert readiness_module._has_resolvable_aws_credentials() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: FAIL — `AttributeError: module 'codemie.service.model_provider_readiness' has no attribute '_has_resolvable_aws_credentials'` (and no `boto3` attribute to patch yet)

- [ ] **Step 3: Add the `boto3` import and `_has_resolvable_aws_credentials` helper**

Add near the top of `codemie/src/codemie/service/model_provider_readiness.py` (after the existing imports):

```python
import boto3
```

Add the helper function (anywhere above `check_model_provider_readiness`):

```python
def _has_resolvable_aws_credentials() -> bool:
    """True when boto3's standard credential chain resolves something, locally — no network call."""
    try:
        return boto3.Session().get_credentials() is not None
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS (the 3 new tests, plus all Task 1 tests still passing)

- [ ] **Step 5: Write the failing tests for `_check_aws_bedrock_configured` (explicit Bedrock readiness validation) and its wiring into `check_model_provider_readiness`**

```python
def test_check_aws_bedrock_configured_when_region_and_credentials_present(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "us-east-1", raising=False)
    monkeypatch.setattr(readiness_module, "_has_resolvable_aws_credentials", lambda: True)

    result = readiness_module._check_aws_bedrock_configured()

    assert result == readiness_module.ModelProviderReadiness(
        configured=True, provider="aws_bedrock", missing=[]
    )


def test_check_aws_bedrock_configured_missing_region(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "", raising=False)
    monkeypatch.setattr(readiness_module, "_has_resolvable_aws_credentials", lambda: True)

    result = readiness_module._check_aws_bedrock_configured()

    assert result.configured is False
    assert result.provider == "aws_bedrock"
    assert result.missing == ["AWS_BEDROCK_REGION"]


def test_check_aws_bedrock_configured_missing_credentials(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "us-east-1", raising=False)
    monkeypatch.setattr(readiness_module, "_has_resolvable_aws_credentials", lambda: False)

    result = readiness_module._check_aws_bedrock_configured()

    assert result.configured is False
    assert result.provider == "aws_bedrock"
    assert result.missing == ["AWS credentials (standard credential chain)"]


def test_check_aws_bedrock_configured_missing_both(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "", raising=False)
    monkeypatch.setattr(readiness_module, "_has_resolvable_aws_credentials", lambda: False)

    result = readiness_module._check_aws_bedrock_configured()

    assert result.configured is False
    assert result.provider == "aws_bedrock"
    assert result.missing == ["AWS_BEDROCK_REGION", "AWS credentials (standard credential chain)"]


def test_bedrock_configured_end_to_end(monkeypatch):
    """check_model_provider_readiness must delegate to _check_aws_bedrock_configured for a Bedrock default model."""
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AWS_BEDROCK),
    )
    monkeypatch.setattr(
        readiness_module,
        "_check_aws_bedrock_configured",
        lambda: readiness_module.ModelProviderReadiness(
            configured=True, provider="aws_bedrock", missing=[]
        ),
    )

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        configured=True, provider="aws_bedrock", missing=[]
    )


def test_models_env_aws_with_only_azure_credentials_reports_missing_bedrock_configuration(monkeypatch):
    """MODELS_ENV=aws selects a profile whose default model is Bedrock — Azure credentials alone
    don't satisfy it, so the check must report the missing *Bedrock* configuration, not treat the
    Azure credentials as sufficient. This exercises the real (non-mocked) _check_aws_bedrock_configured
    end to end, mirroring the operator scenario described in the ticket's 'What configured means' note.
    """
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "aws", raising=False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AWS_BEDROCK),
    )
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_API_KEY", "key", raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "https://example", raising=False)
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "", raising=False)
    monkeypatch.setattr(readiness_module, "_has_resolvable_aws_credentials", lambda: False)

    result = readiness_module.check_model_provider_readiness()

    assert result.configured is False
    assert result.provider == "aws_bedrock"
    assert result.missing == ["AWS_BEDROCK_REGION", "AWS credentials (standard credential chain)"]
    assert "AZURE_OPENAI_API_KEY" not in result.missing  # Azure creds being present is irrelevant here
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: FAIL — `AttributeError: module 'codemie.service.model_provider_readiness' has no attribute '_check_aws_bedrock_configured'` for the first five tests; `test_models_env_aws_with_only_azure_credentials_reports_missing_bedrock_configuration` also FAILS (Bedrock-provider default models currently fall through to the Task 1 fallback tail, so `configured` comes back `True` and `missing` comes back `[]` instead of naming `AWS_BEDROCK_REGION`/credentials).

- [ ] **Step 7: Add `_check_aws_bedrock_configured` and wire it into `check_model_provider_readiness`**

In `codemie/src/codemie/service/model_provider_readiness.py`, add the explicit Bedrock validation function (anywhere above `check_model_provider_readiness`, alongside `_has_resolvable_aws_credentials`):

```python
def _check_aws_bedrock_configured() -> ModelProviderReadiness:
    """Explicit AWS Bedrock readiness validation: region setting + local credential-chain resolution."""
    missing = []
    if not config.AWS_BEDROCK_REGION:
        missing.append("AWS_BEDROCK_REGION")
    if not _has_resolvable_aws_credentials():
        missing.append("AWS credentials (standard credential chain)")
    return ModelProviderReadiness(
        configured=not missing, provider=LLMProvider.AWS_BEDROCK.value, missing=missing
    )
```

Then insert the Bedrock branch immediately after the Azure OpenAI branch (before the `if provider is None:` block) in `check_model_provider_readiness()`:

```python
    if provider == LLMProvider.AWS_BEDROCK:
        return _check_aws_bedrock_configured()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS (all tests — 16 total)

- [ ] **Step 9: Commit**

```bash
git add codemie/src/codemie/service/model_provider_readiness.py codemie/tests/codemie/service/test_model_provider_readiness.py
git commit -m "EPMCDME-14653: Add AWS Bedrock branch and local credential-chain check"
```

---

### Task 3: Logging (INFO on configured, WARNING naming missing settings)

**Files:**
- Modify: `codemie/src/codemie/service/model_provider_readiness.py`
- Test: `codemie/tests/codemie/service/test_model_provider_readiness.py`

**Interfaces:**
- Consumes: `ModelProviderReadiness` from Task 1.
- Produces: `def log_model_provider_readiness(result: ModelProviderReadiness) -> None` — called once by Task 4's `main.py` wiring, right after `check_model_provider_readiness()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_log_model_provider_readiness_configured_logs_info_naming_provider(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "azure", raising=False)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)  # "codemie" logger normally isn't
    result = readiness_module.ModelProviderReadiness(configured=True, provider="azure_openai", missing=[])

    with caplog.at_level("INFO"):
        readiness_module.log_model_provider_readiness(result)

    assert "azure_openai" in caplog.text
    assert "azure" in caplog.text  # MODELS_ENV profile name
    assert "WARNING" not in caplog.text


def test_log_model_provider_readiness_not_configured_logs_warning_naming_missing_settings(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "azure", raising=False)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)
    result = readiness_module.ModelProviderReadiness(
        configured=False, provider="azure_openai", missing=["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"]
    )

    with caplog.at_level("WARNING"):
        readiness_module.log_model_provider_readiness(result)

    assert "AZURE_OPENAI_API_KEY" in caplog.text
    assert "AZURE_OPENAI_URL" in caplog.text


def test_log_model_provider_readiness_never_logs_a_credential_value(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "azure", raising=False)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)
    result = readiness_module.ModelProviderReadiness(
        configured=False, provider="azure_openai", missing=["AZURE_OPENAI_API_KEY"]
    )

    with caplog.at_level("WARNING"):
        readiness_module.log_model_provider_readiness(result)

    assert "sk-super-secret-value" not in caplog.text  # sanity: nothing resembling a secret appears
```

**Why `monkeypatch.setattr(readiness_module.logger, "propagate", True)` is required:** `codemie/src/codemie/configs/logger.py` configures the `"codemie"` logger with `propagate: False` and its own dedicated stream handler (`LogConfig.LOGGERS`, line ~78) — this is a deliberate app-wide setting, not something to change in production code. `pytest`'s `caplog` fixture attaches its capture handler to the *root* logger; `caplog.at_level(level, logger=name)` only adjusts the named logger's level, it does **not** attach the capture handler to that logger (verified against `_pytest.logging.LogCaptureFixture.at_level` source in this repo's pytest 8.3.3). With `propagate=False`, records never reach root, so `caplog.text` stays empty regardless of `at_level`'s `logger=` argument. Flipping `propagate` to `True` for the duration of the test (via `monkeypatch`, auto-reverted) is the narrowest fix — it only affects this one logger instance for this one test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: FAIL — `AttributeError: module 'codemie.service.model_provider_readiness' has no attribute 'log_model_provider_readiness'`

- [ ] **Step 3: Implement `log_model_provider_readiness`**

Add to `codemie/src/codemie/service/model_provider_readiness.py`, after `check_model_provider_readiness`:

```python
def log_model_provider_readiness(result: ModelProviderReadiness) -> None:
    """Log the readiness result once at startup. Never logs setting values, only names."""
    if result.configured:
        logger.info(
            f"Model provider readiness: configured. Provider={result.provider}. "
            f"MODELS_ENV={config.MODELS_ENV}."
        )
    else:
        logger.warning(
            f"Model provider readiness: NOT configured. Missing: {', '.join(result.missing)}. "
            f"MODELS_ENV={config.MODELS_ENV}. The first chat request will fail until this is resolved."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS (all tests — 19 total)

- [ ] **Step 5: Commit**

```bash
git add codemie/src/codemie/service/model_provider_readiness.py codemie/tests/codemie/service/test_model_provider_readiness.py
git commit -m "EPMCDME-14653: Log model-provider readiness once at startup (INFO/WARNING)"
```

---

### Task 4: Wire into `lifespan()` via a new `_check_model_provider_readiness(app)` helper

**Files:**
- Modify: `codemie/src/codemie/rest_api/main.py`
- Test: `codemie/tests/codemie/rest_api/test_startup_integration.py`

**Interfaces:**
- Consumes: `check_model_provider_readiness`, `log_model_provider_readiness` from `codemie.service.model_provider_readiness` (Tasks 1-3).
- Produces: `def _check_model_provider_readiness(app: FastAPI) -> None` in `main.py`, called from `lifespan()`; sets `app.state.model_provider_readiness` (a `ModelProviderReadiness`), consumed by Task 5's `healthcheck()`.

- [ ] **Step 1: Write the failing test**

Append to `codemie/tests/codemie/rest_api/test_startup_integration.py`:

```python
def test_check_model_provider_readiness_stores_result_on_app_state_and_logs():
    from fastapi import FastAPI

    from codemie.rest_api import main
    from codemie.service.model_provider_readiness import ModelProviderReadiness

    app = FastAPI()
    fake_result = ModelProviderReadiness(configured=True, provider="azure_openai", missing=[])
    logged = []

    with patch("codemie.rest_api.main.check_model_provider_readiness", return_value=fake_result):
        with patch("codemie.rest_api.main.log_model_provider_readiness", side_effect=logged.append):
            main._check_model_provider_readiness(app)

    assert app.state.model_provider_readiness == fake_result
    assert logged == [fake_result]


def test_check_model_provider_readiness_does_not_raise_when_not_configured():
    """The app must still start when no model provider is configured."""
    from fastapi import FastAPI

    from codemie.rest_api import main
    from codemie.service.model_provider_readiness import ModelProviderReadiness

    app = FastAPI()
    fake_result = ModelProviderReadiness(
        configured=False, provider=None, missing=["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"]
    )

    with patch("codemie.rest_api.main.check_model_provider_readiness", return_value=fake_result):
        with patch("codemie.rest_api.main.log_model_provider_readiness"):
            main._check_model_provider_readiness(app)  # must not raise

    assert app.state.model_provider_readiness == fake_result
```

(`patch` is already imported at the top of this test file via `from unittest.mock import AsyncMock, MagicMock, patch`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest codemie/tests/codemie/rest_api/test_startup_integration.py -v -k test_check_model_provider_readiness`
Expected: FAIL — `AttributeError: <module 'codemie.rest_api.main'> does not have the attribute 'check_model_provider_readiness'` (patch target doesn't exist yet) and/or `AttributeError: module 'codemie.rest_api.main' has no attribute '_check_model_provider_readiness'`

- [ ] **Step 3: Add the import and the helper function to `main.py`**

In `codemie/src/codemie/rest_api/main.py`, extend the existing import block at lines 113-115 (currently `from codemie.service.oauth_security import (...)`) — add a new import line directly after it:

```python
from codemie.service.model_provider_readiness import (
    check_model_provider_readiness,
    log_model_provider_readiness,
)
```

Add the helper function near the other `_initialize_*`/`_setup_*` private helpers (e.g. directly above `_initialize_enterprise_services` at line 283, or any other helper in that group — placement among siblings is not load-bearing):

```python
def _check_model_provider_readiness(app: FastAPI) -> None:
    """Compute model-provider startup readiness once, store it, and log it.

    Never blocks startup — the app must start whether or not a provider is configured
    (EPMCDME-14653).
    """
    readiness = check_model_provider_readiness()
    app.state.model_provider_readiness = readiness
    log_model_provider_readiness(readiness)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest codemie/tests/codemie/rest_api/test_startup_integration.py -v -k test_check_model_provider_readiness`
Expected: PASS

- [ ] **Step 5: Call the helper from `lifespan()`**

In `codemie/src/codemie/rest_api/main.py`, in `lifespan()`, insert one line immediately after line 790 (`warn_insecure_oauth_storage_for_enabled_providers()`) and before line 791 (`_initialize_deployment_version()`):

```python
    assert_oauth_state_signing_secret_configured()
    assert_token_vault_available()
    warn_insecure_oauth_storage_for_enabled_providers()
    _check_model_provider_readiness(app)
    _initialize_deployment_version()
```

- [ ] **Step 6: Run the full startup integration test file to confirm no regressions**

Run: `pytest codemie/tests/codemie/rest_api/test_startup_integration.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones)

- [ ] **Step 7: Commit**

```bash
git add codemie/src/codemie/rest_api/main.py codemie/tests/codemie/rest_api/test_startup_integration.py
git commit -m "EPMCDME-14653: Compute model-provider readiness once in lifespan(), store on app.state"
```

---

### Task 5: Surface readiness on `GET /healthcheck`

**Files:**
- Modify: `codemie/src/codemie/rest_api/routers/common.py`
- Test: `codemie/tests/codemie/rest_api/routers/test_common_healthcheck.py` (new)

**Interfaces:**
- Consumes: `app.state.model_provider_readiness` (a `ModelProviderReadiness`, set by Task 4's `_check_model_provider_readiness`).
- Produces: `healthcheck()` response gains a `model_provider` key: `{"status": "configured" | "not_configured", "provider": <str | None>}`.

- [ ] **Step 1: Write the failing test**

Create `codemie/tests/codemie/rest_api/routers/test_common_healthcheck.py`. Note: this repo's existing router
tests (`test_common.py`, `test_common_router.py`) use `httpx.AsyncClient` + `ASGITransport` with
`@pytest.mark.asyncio`, not `fastapi.testclient.TestClient` — match that convention rather than
introducing a sync client:

```python
"""GET /healthcheck must report model-provider readiness without ever failing the probe."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from codemie.rest_api.routers.common import router
from codemie.service.model_provider_readiness import ModelProviderReadiness


def _app_with_readiness(readiness: ModelProviderReadiness) -> FastAPI:
    app = FastAPI()
    app.state.model_provider_readiness = readiness
    app.include_router(router)  # router already carries prefix="/v1" (see main.py:895 precedent)
    return app


@pytest.mark.asyncio
async def test_healthcheck_reports_configured_provider():
    app = _app_with_readiness(ModelProviderReadiness(configured=True, provider="azure_openai", missing=[]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/healthcheck")

    assert response.status_code == 200
    assert response.json()["model_provider"] == {"status": "configured", "provider": "azure_openai"}


@pytest.mark.asyncio
async def test_healthcheck_reports_not_configured_with_http_200():
    app = _app_with_readiness(
        ModelProviderReadiness(
            configured=False, provider=None, missing=["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"]
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/healthcheck")

    assert response.status_code == 200
    assert response.json()["model_provider"] == {"status": "not_configured", "provider": None}
    # setting-level detail (which vars are missing) stays out of the unauthenticated endpoint
    assert "AZURE_OPENAI_API_KEY" not in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest codemie/tests/codemie/rest_api/routers/test_common_healthcheck.py -v`
Expected: FAIL — `KeyError: 'model_provider'` (field doesn't exist yet)

- [ ] **Step 3: Update `healthcheck()`**

In `codemie/src/codemie/rest_api/routers/common.py`:

Change the import line at the top from:

```python
from fastapi import APIRouter, Depends, status
```

to:

```python
from fastapi import APIRouter, Depends, Request, status
```

Replace the `healthcheck()` function (lines 54-65) with:

```python
@router.get("/healthcheck", include_in_schema=False)
def healthcheck(request: Request):
    import os
    import psutil

    process = psutil.Process(os.getpid())
    readiness = request.app.state.model_provider_readiness
    return {
        "status": "healthy",
        "memory_usage_mb": process.memory_info().rss / 1024 / 1024,
        "cpu_percent": process.cpu_percent(),
        "worker_pid": os.getpid(),
        "model_provider": {
            "status": "configured" if readiness.configured else "not_configured",
            "provider": readiness.provider,
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest codemie/tests/codemie/rest_api/routers/test_common_healthcheck.py -v`
Expected: PASS

- [ ] **Step 5: Run the full common router test file (if any pre-existing tests cover `/info` or `/deployment-versions` in this router) and the startup integration suite, to confirm no regressions**

Run: `pytest codemie/tests/codemie/rest_api/routers/test_common_healthcheck.py codemie/tests/codemie/rest_api/test_startup_integration.py codemie/tests/codemie/service/test_model_provider_readiness.py -v`
Expected: PASS (all tests across all three files)

- [ ] **Step 6: Commit**

```bash
git add codemie/src/codemie/rest_api/routers/common.py codemie/tests/codemie/rest_api/routers/test_common_healthcheck.py
git commit -m "EPMCDME-14653: Surface model_provider readiness on GET /healthcheck"
```

---

## Post-plan manual verification (not automated by this plan)

Documented in the ticket for the operator/reviewer to run manually, using `C:\AI_RUN_BootCamp_2026\CODEMIE\docs2\tests_ignore.ps1` for full validation, and `make verify CONTAINER_RUNTIME=podman` for the Makefile gate (Podman, not Docker, is used locally):

1. One credential set (Azure OpenAI or AWS Bedrock) with the matching `MODELS_ENV` → app starts, startup log names the active provider, `/healthcheck` reports `configured`, chat answers end to end.
2. No model provider configured → app starts, startup WARNING names the missing settings, `/healthcheck` reports `not_configured` with HTTP 200, and the operator sees this before attempting a first chat.
3. Credentials present but `MODELS_ENV` pointing at a different profile → reported as not configured, mismatch named in the log.

---

## Deviations from this plan

The plan above is preserved as written. The implementation departed from it in the places
listed below. Where the two disagree, **the source code is authoritative** — each deviation
was a deliberate correction, not drift.

### Behavioral deviations

**D1 — Result type is a tri-state, not a boolean.**
Planned (line 31, line 90): `ModelProviderReadiness.configured: bool`.
Actual: `status: ModelProviderStatus` with `CONFIGURED` / `NOT_CONFIGURED` / `NOT_CHECKED`
(`model_provider_readiness.py:34-43`).
A boolean can only say yes or no, which forced providers the check never validates (LiteLLM,
Vertex AI, Anthropic) to be reported as `configured` — a claim nothing verified. The ticket
requires those be reported as not checked, and requires that they never be falsely reported as
`configured` or `not_configured`. Every downstream signature changed with it: the log function,
the `app.state` value, and the `/healthcheck` payload.

**D2 — The LiteLLM branch also checks `LLM_PROXY_MODE`.**
Planned (line 97, and the rationale at line 110): gate on `is_litellm_enabled()` alone,
explicitly *not* re-deriving the `LLM_PROXY_ENABLED`/`LLM_PROXY_MODE` condition, on the argument
that this keeps a single source of truth with `main.py`.
Actual: `if is_litellm_enabled() and config.LLM_PROXY_MODE == "lite_llm":`
(`model_provider_readiness.py:77`).
The plan's premise was half right. `is_litellm_enabled()` is the single source of truth for
*whether the enterprise package is present and enabled* (`HAS_LITELLM and LLM_PROXY_ENABLED`,
`enterprise/litellm/dependencies.py:66-70`) — but that is not the condition under which chat
routes through the proxy. `get_litellm_chat_model()` requires both that function **and**
`LLMProxyMode.lite_llm == config.LLM_PROXY_MODE` (`enterprise/litellm/llm_factory.py:1083-1088`,
mirrored for embeddings at `1137-1142`). With `LLM_PROXY_ENABLED=true` and `LLM_PROXY_MODE` left
at its default `internal`, the planned check would have reported
`provider="litellm_proxy", configured=True` while chat actually routed through the native
Azure/Bedrock path — the exact "first-message surprise" this ticket exists to remove. Raised in
review as CR-003, corrected in commit `be7fcc479`. Readiness activation now matches the chat
runtime criteria exactly, which is what the ticket asks for.

**D3 — The LiteLLM branch reports `not_checked`, not `configured`.**
Planned (line 98): `ModelProviderReadiness(configured=True, provider="litellm_proxy", missing=[])`.
Actual: `status=NOT_CHECKED, provider="litellm_proxy"` (`model_provider_readiness.py:78-82`).
Nothing about the LiteLLM runtime configuration is validated, so `configured` overstated what the
check knew. The ticket specifies `status="not_checked"` with `provider="litellm_proxy"`, and
forbids validating or altering LiteLLM runtime configuration.

**D4 — Non-Azure/Bedrock providers report `not_checked`, not `configured`.**
Planned (line 268): `return ModelProviderReadiness(configured=True, provider=provider.value, missing=[])`.
Actual: `status=NOT_CHECKED` (`model_provider_readiness.py:116-120`).
Same reasoning as D3. Vertex AI, Anthropic, and Vertex-Anthropic deployments keep their existing
runtime behavior and are reported honestly as unvalidated.

**D5 — Bedrock region resolution goes through `boto3.Session`, not a truthiness test.**
Planned (line 458): `if not config.AWS_BEDROCK_REGION: missing.append("AWS_BEDROCK_REGION")`.
Actual: `boto3.Session(region_name=config.AWS_BEDROCK_REGION.strip() or None)`, then a check on
the resolved `session.region_name` (`model_provider_readiness.py:52-58`).
The planned version made `AWS_BEDROCK_REGION` mandatory, which contradicts the runtime it is
meant to describe: `get_bedrock_runtime_client()` passes `region_name` **only** when the setting
is non-empty, and otherwise lets boto3 resolve the region from `AWS_REGION`, `AWS_DEFAULT_REGION`,
or shared config (`core/dependecies.py:173-180`). A valid EKS deployment that sets only
`AWS_REGION` would have been reported `not_configured` while chat worked. The ticket requires
resolving the region "using `AWS_BEDROCK_REGION` when supplied, otherwise standard boto3 region
resolution".

**D6 — Azure settings are checked for non-blankness, not falsiness.**
Planned (Task 1, Step 9): `if not config.AZURE_OPENAI_API_KEY`.
Actual: `_is_present()`, which strips before testing (`model_provider_readiness.py:46-47, 96-99`).
The ticket says *nonblank*. A Helm value of `" "` is a real misconfiguration that the planned
check would have accepted as present.

**D7 — The startup helper swallows its own failures.**
Planned (lines 655-664): `_check_model_provider_readiness()` calls the check directly, with no
exception handling.
Actual: wrapped in `try/except Exception` (`rest_api/main.py:296-306`).
A bug anywhere in the readiness check — including the boto3 call — would have propagated out of
`lifespan()` and prevented the application from starting, defeating the ticket's central
requirement that the app starts either way. The failure path reports `NOT_CHECKED` (not
`NOT_CONFIGURED`, since nothing was actually determined) with the fixed reason
`"model provider readiness check failed; see startup logs"`. The exception itself goes only to
`logger.exception`, so no raw exception text can reach `/healthcheck`. Raised as CR-001.

**D8 — `healthcheck()` reads `app.state` defensively and returns `missing`.**
Planned (lines 780-790): `readiness = request.app.state.model_provider_readiness`, and a two-key
`model_provider` object (`status`, `provider`).
Actual: `getattr(request.app.state, "model_provider_readiness", None)` with an `"unknown"`
fallback, plus the `missing` array (`rest_api/routers/common.py:60-68`).
Any app that mounts this router without running `main.py`'s `lifespan()` — every router-level
test, for one — would have raised `AttributeError` and turned a Kubernetes probe endpoint into an
HTTP 500. All three probes point at `/v1/healthcheck`
(`deploy-templates/values.yaml:506,517,532`), so this had to be unconditionally safe. Raised as
CR-002. `missing` was added because ticket requirement 6 asks the endpoint itself to name the
missing setting categories, which the planned two-key shape could not carry.

**D9 — `log_model_provider_readiness` has four branches, not two.**
Planned (lines 551-566): INFO when configured, WARNING otherwise.
Actual: INFO on `CONFIGURED`; WARNING on `NOT_CONFIGURED`; WARNING on `NOT_CHECKED` **with** a
reason (the D7 failure path, which an operator must see); INFO on plain `NOT_CHECKED`
(`model_provider_readiness.py:123-143`). Follows directly from D1.

### Structural deviations

**D10 — `_has_resolvable_aws_credentials()` was not built.**
Planned (lines 294-324): a standalone `-> bool` helper with three of its own unit tests, called by
`_check_aws_bedrock_configured()`.
Actual: credential resolution is inline in `_check_aws_bedrock_configured()`
(`model_provider_readiness.py:60-66`).
D5 requires one `boto3.Session` shared between region resolution and credential resolution; a
bool-returning helper that constructed its own bare `boto3.Session()` could no longer serve. Its
three planned tests were superseded by five `_check_aws_bedrock_configured` tests covering the
same credential cases plus the region cases. `_check_aws_bedrock_configured()` itself was built as
planned and remains independently testable, which was the point of the split.

**D11 — Test names and count differ.**
Planned: 16 tests in `test_model_provider_readiness.py`. Actual: 20.
Renamed to match the tri-state semantics —
`test_litellm_proxy_mode_reports_configured_without_inspecting_credentials` →
`..._reports_not_checked_...`;
`test_other_provider_reported_configured_without_credential_enforcement` →
`..._reported_not_checked_...`;
`test_check_aws_bedrock_configured_missing_region` → `..._missing_resolvable_region`.
Added beyond the plan:
`test_litellm_enabled_but_internal_mode_falls_through_to_native_check` (D2),
`test_check_aws_bedrock_uses_standard_region_when_dedicated_region_is_empty` (D5),
`test_models_env_aws_with_only_azure_credentials_reports_missing_bedrock_configuration` (D13),
and two `not_checked` logging tests (D9).

**D12 — Startup-integration coverage went beyond Task 4, Step 1.**
Planned: one test driving `_check_model_provider_readiness()` directly.
Added: `test_check_model_provider_readiness_swallows_exceptions_and_still_starts` (D7),
`test_lifespan_computes_model_provider_readiness_and_stores_it_on_app_state`, and a
`_STUB_READINESS` patch in the lifespan fixture and in the `ExitStack` test.
The helper-level tests all stayed green with the `lifespan()` call site deleted — readiness would
have silently never run while the suite reported success. Separately, the lifespan tests were
executing the *real* check, which for a Bedrock-default profile resolves the live AWS credential
chain; they now stub it.

**D13 — Manual verification item 3 is now automated.**
Planned (final section): "credentials present but `MODELS_ENV` pointing at a different profile"
was listed as a manual reviewer step. It is now covered by
`test_models_env_aws_with_only_azure_credentials_reports_missing_bedrock_configuration`
(`tests/codemie/service/test_model_provider_readiness.py:260`), which exercises the real
`_check_aws_bedrock_configured()` end to end. Manual items 1 and 2 remain manual.

### Corrected premise

**D14 — Credential resolution is not guaranteed to be network-free.**
The Global Constraints (line 17) assert that `boto3.Session().get_credentials()` "is a local
resolution ... not a call to a provider API". That is not reliably true: the standard chain can
contact the EC2/ECS metadata service or STS while resolving a role. The constraint's *intent*
holds and is met — no Bedrock or Azure **model** API is ever called — but the module docstring
now states the real behavior (`model_provider_readiness.py:17-20`), matching the ticket's own
acknowledgement that "credential resolution may contact STS or metadata endpoints".

### Constraints held as written

The remaining Global Constraints were honored: no secret values are logged or returned (only
setting names); `/healthcheck` returns HTTP 200 in every state; the LLM profile YAML is never
re-parsed; and the unrelated uncommitted `Makefile` change was left untouched.

### Amendments from MR review

**D15 — The IMDS leg is pinned, and an unresolvable chain reports `not_checked`.**
Raised in MR review against D14: `get_credentials()` can reach ECS container credentials or EC2
IMDS, and it runs synchronously inside `lifespan()`, so an unreachable metadata endpoint delays
startup.
Actual: readiness now builds its session through `_bedrock_session()`
(`model_provider_readiness.py:68-84`), which pins `metadata_service_timeout` and
`metadata_service_num_attempts` to botocore's own defaults of 1 second and 1 attempt. Those
defaults are otherwise operator-overridable through `AWS_METADATA_SERVICE_TIMEOUT` and
`AWS_METADATA_SERVICE_NUM_ATTEMPTS`; pinning them means no environment can widen the IMDS lookup.

This is deliberately *not* an overall deadline for the credential chain, and the documentation
should not be read as claiming one — a follow-up note from the same MR review. Those two settings
reach the instance-metadata provider only (`botocore/credentials.py:93`, `botocore/utils.py:759`).
The other providers that perform work during resolution carry their own separate limits: the
ECS/EKS container fetcher uses botocore's fixed `ContainerMetadataFetcher.TIMEOUT_SECONDS = 2` with
`RETRY_ATTEMPTS = 3`, and a profile configured with `credential_process` runs an external command
through `subprocess.Popen(...).communicate()` with no timeout at all. Assume-role and web-identity
providers return `DeferredRefreshableCredentials`, so no STS call happens during this check.

Separately, `_check_aws_bedrock_configured()` no longer treats a raised exception as
"credentials are missing" (`model_provider_readiness.py:76-86`): a chain that fails to resolve
establishes nothing, so the result is `NOT_CHECKED` with the sanitized reason
`"AWS credential chain could not be resolved; see startup logs"`, preserving any region finding
alongside it. The full exception goes to `logger.exception` only.

Moving the call off the event loop (`asyncio.to_thread`) was considered and rejected: `lifespan()`
awaits its startup sequence either way, so threading changes which thread blocks but not how long
startup takes. Pinning the IMDS leg is what actually addresses the concern for the deployment
shapes this ticket targets; a `credential_process` profile would still be unbounded, and no
in-process setting can change that.

The standard credential chain itself is retained rather than replaced with a purely local
environment-variable probe, because the ticket requires resolving credentials "through the
standard boto3 credential chain" and explicitly recognises that "credential resolution may contact
STS or metadata endpoints". A local-only probe would report `not_checked` for every EC2
instance-role deployment, which is strictly less useful and still not what the ticket asked for.
