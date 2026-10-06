# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Model-provider startup readiness must name what's missing, and must never block startup."""

from unittest.mock import MagicMock, patch

import codemie.service.model_provider_readiness as readiness_module
from codemie.configs.llm_config import LLMModel, LLMProvider


def _model(provider):
    return LLMModel(base_name="m", deployment_name="m", enabled=True, provider=provider)


def _fake_session(region, credentials=None, credentials_error=None):
    """Stand-in for the bounded boto3 session built by _bedrock_session()."""
    session = MagicMock()
    session.region_name = region
    if credentials_error is not None:
        session.get_credentials.side_effect = credentials_error
    else:
        session.get_credentials.return_value = credentials
    return session


def test_no_global_default_model_reports_not_configured(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "some-profile", raising=False)
    monkeypatch.setattr(readiness_module.llm_service, "get_default_model_for_category", lambda category: None)

    result = readiness_module.check_model_provider_readiness()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
    assert result.provider is None
    assert result.missing == [readiness_module.MISSING_GLOBAL_DEFAULT_MODEL]
    # names the setting, never its configured value — this list is served on an unauthenticated endpoint
    assert "some-profile" not in result.missing[0]


def test_litellm_proxy_mode_reports_not_checked_without_inspecting_credentials(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: True)
    monkeypatch.setattr(readiness_module.config, "LLM_PROXY_MODE", "lite_llm", raising=False)

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.NOT_CHECKED,
        provider="litellm_proxy",
        missing=[],
    )


def test_litellm_enabled_but_internal_mode_falls_through_to_native_check(monkeypatch):
    """CR-003: LLM_PROXY_ENABLED=true with LLM_PROXY_MODE left at its default 'internal' means chat
    actually routes through the internal Azure/Bedrock path (llm_factory.py), not LiteLLM — is_litellm_enabled()
    alone must not short-circuit to a false 'configured, provider=litellm_proxy'.
    """
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: True)
    monkeypatch.setattr(readiness_module.config, "LLM_PROXY_MODE", "internal", raising=False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AZURE_OPENAI),
    )
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_API_KEY", "key", raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "https://example", raising=False)

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.CONFIGURED,
        provider="azure_openai",
        missing=[],
    )


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
        status=readiness_module.ModelProviderStatus.CONFIGURED,
        provider="azure_openai",
        missing=[],
    )


def test_azure_missing_api_key(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AZURE_OPENAI),
    )
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_API_KEY", "   ", raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "https://example", raising=False)

    result = readiness_module.check_model_provider_readiness()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
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
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "   ", raising=False)

    result = readiness_module.check_model_provider_readiness()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
    assert result.provider == "azure_openai"
    assert result.missing == ["AZURE_OPENAI_URL"]


def test_other_provider_reported_not_checked_without_credential_enforcement(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.GOOGLE_VERTEX_AI),
    )

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.NOT_CHECKED,
        provider="google_vertexai",
        missing=[],
    )


def test_unset_provider_reports_not_configured(monkeypatch):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: LLMModel(base_name="secret-deployment", deployment_name="d", enabled=True, provider=None),
    )

    result = readiness_module.check_model_provider_readiness()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
    assert result.provider is None
    assert result.missing == [readiness_module.MISSING_MODEL_PROVIDER]
    # the model name stays in the startup log, out of the unauthenticated health response
    assert "secret-deployment" not in result.missing[0]


def test_missing_global_default_model_names_the_profile_in_the_startup_log(monkeypatch, caplog):
    """Detail dropped from the health response must not be lost — it moves to the internal log."""
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "some-profile", raising=False)
    monkeypatch.setattr(readiness_module.llm_service, "get_default_model_for_category", lambda category: None)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)

    with caplog.at_level("WARNING"):
        readiness_module.check_model_provider_readiness()

    assert "some-profile" in caplog.text


def test_unset_provider_names_the_model_in_the_startup_log(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: LLMModel(base_name="secret-deployment", deployment_name="d", enabled=True, provider=None),
    )
    monkeypatch.setattr(readiness_module.logger, "propagate", True)

    with caplog.at_level("WARNING"):
        readiness_module.check_model_provider_readiness()

    assert "secret-deployment" in caplog.text


def test_bedrock_session_bounds_the_metadata_lookup_and_passes_the_dedicated_region():
    """An unreachable IMDS endpoint must not stretch startup.

    botocore's own defaults are 1s / 1 attempt, but both are operator-overridable via
    AWS_METADATA_SERVICE_TIMEOUT / AWS_METADATA_SERVICE_NUM_ATTEMPTS. Pinning them keeps the IMDS
    leg bounded regardless of the deployment's environment. This is not an overall deadline for the
    credential chain — see _bedrock_session's docstring for the providers with their own limits.
    """
    botocore_session = MagicMock()

    with patch("codemie.service.model_provider_readiness.botocore.session.Session", return_value=botocore_session):
        with patch("codemie.service.model_provider_readiness.boto3.Session") as session_factory:
            readiness_module._bedrock_session("us-east-1")

    botocore_session.set_config_variable.assert_any_call("metadata_service_timeout", 1)
    botocore_session.set_config_variable.assert_any_call("metadata_service_num_attempts", 1)
    session_factory.assert_called_once_with(botocore_session=botocore_session, region_name="us-east-1")


def test_bedrock_session_falls_back_to_standard_region_resolution_when_dedicated_region_is_empty():
    """Mirrors get_bedrock_runtime_client(), which omits region_name entirely when the setting is empty."""
    with patch("codemie.service.model_provider_readiness.botocore.session.Session") as botocore_factory:
        with patch("codemie.service.model_provider_readiness.boto3.Session") as session_factory:
            readiness_module._bedrock_session("")

    session_factory.assert_called_once_with(botocore_session=botocore_factory.return_value, region_name=None)


def test_check_aws_bedrock_configured_when_region_and_credentials_present(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", " us-east-1 ", raising=False)
    seen_regions = []

    def fake_bedrock_session(region):
        seen_regions.append(region)
        return _fake_session("us-east-1", credentials=MagicMock())

    monkeypatch.setattr(readiness_module, "_bedrock_session", fake_bedrock_session)

    result = readiness_module._check_aws_bedrock_configured()

    assert seen_regions == ["us-east-1"]  # the dedicated setting is stripped before use
    assert result == readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.CONFIGURED,
        provider="aws_bedrock",
        missing=[],
    )


def test_check_aws_bedrock_uses_standard_region_when_dedicated_region_is_empty(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "", raising=False)
    monkeypatch.setattr(
        readiness_module,
        "_bedrock_session",
        lambda region: _fake_session("us-west-2", credentials=MagicMock()),
    )

    result = readiness_module._check_aws_bedrock_configured()

    assert result.status == readiness_module.ModelProviderStatus.CONFIGURED
    assert result.provider == "aws_bedrock"
    assert result.missing == []


def test_check_aws_bedrock_configured_missing_resolvable_region(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "   ", raising=False)
    monkeypatch.setattr(
        readiness_module,
        "_bedrock_session",
        lambda region: _fake_session(None, credentials=MagicMock()),
    )

    result = readiness_module._check_aws_bedrock_configured()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
    assert result.provider == "aws_bedrock"
    assert result.missing == [readiness_module.MISSING_AWS_REGION]


def test_check_aws_bedrock_configured_missing_credentials(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "us-east-1", raising=False)
    monkeypatch.setattr(
        readiness_module,
        "_bedrock_session",
        lambda region: _fake_session("us-east-1", credentials=None),
    )

    result = readiness_module._check_aws_bedrock_configured()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
    assert result.provider == "aws_bedrock"
    assert result.missing == [readiness_module.MISSING_AWS_CREDENTIALS]


def test_check_aws_bedrock_credential_resolution_failure_reports_not_checked(monkeypatch):
    """A chain that fails to resolve says nothing about whether credentials exist.

    Reporting not_configured would assert something the check never established — an unreachable
    metadata endpoint on a host that has no AWS identity at all looks identical to one on a host
    whose IRSA token is momentarily unreadable.
    """
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "us-east-1", raising=False)
    monkeypatch.setattr(
        readiness_module,
        "_bedrock_session",
        lambda region: _fake_session("us-east-1", credentials_error=RuntimeError("metadata unavailable")),
    )

    result = readiness_module._check_aws_bedrock_configured()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CHECKED
    assert result.provider == "aws_bedrock"
    assert result.missing == [readiness_module.UNRESOLVED_AWS_CREDENTIALS]
    assert "metadata unavailable" not in result.missing[0]  # no raw exception text escapes


def test_check_aws_bedrock_credential_failure_still_reports_the_missing_region(monkeypatch):
    monkeypatch.setattr(readiness_module.config, "AWS_BEDROCK_REGION", "", raising=False)
    monkeypatch.setattr(
        readiness_module,
        "_bedrock_session",
        lambda region: _fake_session(None, credentials_error=RuntimeError("metadata unavailable")),
    )

    result = readiness_module._check_aws_bedrock_configured()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CHECKED
    assert result.provider == "aws_bedrock"
    assert result.missing == [
        readiness_module.MISSING_AWS_REGION,
        readiness_module.UNRESOLVED_AWS_CREDENTIALS,
    ]


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
            status=readiness_module.ModelProviderStatus.CONFIGURED,
            provider="aws_bedrock",
            missing=[],
        ),
    )

    result = readiness_module.check_model_provider_readiness()

    assert result == readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.CONFIGURED,
        provider="aws_bedrock",
        missing=[],
    )


def test_models_env_aws_with_only_azure_credentials_reports_missing_bedrock_configuration(monkeypatch):
    """MODELS_ENV=aws selects a profile whose default model is Bedrock — Azure credentials alone
    don't satisfy it, so the check must report the missing *Bedrock* configuration, not treat the
    Azure credentials as sufficient. Exercises the real (non-mocked) _check_aws_bedrock_configured
    end to end.
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
    monkeypatch.setattr(
        readiness_module,
        "_bedrock_session",
        lambda region: _fake_session(None, credentials=None),
    )

    result = readiness_module.check_model_provider_readiness()

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
    assert result.provider == "aws_bedrock"
    assert result.missing == [
        readiness_module.MISSING_AWS_REGION,
        readiness_module.MISSING_AWS_CREDENTIALS,
    ]
    assert "AZURE_OPENAI_API_KEY" not in result.missing  # Azure creds being present is irrelevant here


def test_log_model_provider_readiness_configured_logs_info_naming_provider(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "azure", raising=False)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)  # "codemie" logger normally isn't
    result = readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.CONFIGURED,
        provider="azure_openai",
        missing=[],
    )

    with caplog.at_level("INFO"):
        readiness_module.log_model_provider_readiness(result)

    assert "azure_openai" in caplog.text
    assert "azure" in caplog.text  # MODELS_ENV profile name
    assert "WARNING" not in caplog.text


def test_log_model_provider_readiness_not_configured_logs_warning_naming_missing_settings(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "azure", raising=False)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)
    result = readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.NOT_CONFIGURED,
        provider="azure_openai",
        missing=["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"],
    )

    with caplog.at_level("WARNING"):
        readiness_module.log_model_provider_readiness(result)

    assert "AZURE_OPENAI_API_KEY" in caplog.text
    assert "AZURE_OPENAI_URL" in caplog.text


def test_a_configured_credential_value_never_reaches_the_log_or_the_health_payload(monkeypatch, caplog):
    """The secret is genuinely configured here, so the assertions can actually fail.

    Asserting a value is absent without ever setting it proves nothing. AZURE_OPENAI_API_KEY holds a
    real value and AZURE_OPENAI_URL is blank, so the check runs the not_configured path with a live
    secret in config, and both the WARNING log and the `missing` array served on the unauthenticated
    /v1/healthcheck must name the setting without carrying its value.
    """
    secret_key = "sk-super-secret-value"
    monkeypatch.setattr(readiness_module, "is_litellm_enabled", lambda: False)
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "azure", raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_API_KEY", secret_key, raising=False)
    monkeypatch.setattr(readiness_module.config, "AZURE_OPENAI_URL", "", raising=False)
    monkeypatch.setattr(
        readiness_module.llm_service,
        "get_default_model_for_category",
        lambda category: _model(LLMProvider.AZURE_OPENAI),
    )
    monkeypatch.setattr(readiness_module.logger, "propagate", True)

    with caplog.at_level("DEBUG"):
        result = readiness_module.check_model_provider_readiness()
        readiness_module.log_model_provider_readiness(result)

    assert result.status == readiness_module.ModelProviderStatus.NOT_CONFIGURED
    assert result.missing == ["AZURE_OPENAI_URL"]  # the setting name, and only the name
    assert "AZURE_OPENAI_API_KEY" not in caplog.text  # present, so not reported missing
    assert secret_key not in caplog.text
    assert all(secret_key not in entry for entry in result.missing)


def test_log_model_provider_readiness_not_checked_logs_info(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "gcp", raising=False)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)
    result = readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.NOT_CHECKED,
        provider="google_vertexai",
        missing=[],
    )

    with caplog.at_level("INFO"):
        readiness_module.log_model_provider_readiness(result)

    assert "not checked" in caplog.text
    assert "google_vertexai" in caplog.text
    assert "WARNING" not in caplog.text


def test_log_model_provider_readiness_failed_check_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(readiness_module.config, "MODELS_ENV", "azure", raising=False)
    monkeypatch.setattr(readiness_module.logger, "propagate", True)
    result = readiness_module.ModelProviderReadiness(
        status=readiness_module.ModelProviderStatus.NOT_CHECKED,
        provider=None,
        missing=["model provider readiness check failed; see startup logs"],
    )

    with caplog.at_level("WARNING"):
        readiness_module.log_model_provider_readiness(result)

    assert "not checked" in caplog.text
    assert "readiness check failed" in caplog.text
    assert "WARNING" in caplog.text
