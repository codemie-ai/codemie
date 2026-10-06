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

"""Startup readiness check for the configured model provider.

Azure OpenAI and AWS Bedrock configuration is validated without calling their model
APIs. Resolving the AWS credential chain may contact container metadata or instance
metadata; the IMDS leg is pinned to 1s / 1 attempt here, but the chain as a whole has
no single hard deadline (see _bedrock_session). A resolution that fails is reported as
"not checked" rather than asserted to be missing credentials. Other supported providers
are reported as not checked so this diagnostic does not change or overstate their
runtime behavior.
"""

from enum import Enum
from typing import NamedTuple

import boto3
import botocore.session

from codemie.configs import config, logger
from codemie.configs.llm_config import LLMProvider, ModelCategory
from codemie.enterprise.litellm import is_litellm_enabled
from codemie.service.llm_service.llm_service import llm_service


class ModelProviderStatus(str, Enum):
    CONFIGURED = "configured"
    NOT_CONFIGURED = "not_configured"
    NOT_CHECKED = "not_checked"


class ModelProviderReadiness(NamedTuple):
    status: ModelProviderStatus
    provider: str | None
    missing: list[str]


# Every entry names a *setting*, never a configured value: `missing` is returned verbatim on the
# unauthenticated GET /v1/healthcheck. Profile names and model names belong in the startup log only.
MISSING_AWS_REGION = "AWS region (AWS_BEDROCK_REGION or the standard AWS region configuration)"
MISSING_AWS_CREDENTIALS = "AWS credentials (environment, shared config, IRSA, container identity, or IMDS)"
UNRESOLVED_AWS_CREDENTIALS = "AWS credential chain could not be resolved; see startup logs"
MISSING_GLOBAL_DEFAULT_MODEL = "MODELS_ENV (no enabled global default model in the selected profile)"
MISSING_MODEL_PROVIDER = "provider (unset for the selected default model in the model profile)"

# botocore's own defaults, pinned so an operator-set AWS_METADATA_SERVICE_TIMEOUT or
# AWS_METADATA_SERVICE_NUM_ATTEMPTS cannot stretch the IMDS leg of the credential chain.
_METADATA_TIMEOUT_SECONDS = 1
_METADATA_NUM_ATTEMPTS = 1


def _is_present(value: str) -> bool:
    return bool(value.strip())


def _bedrock_session(region: str) -> boto3.Session:
    """Build the session used for readiness only, with the IMDS leg of the credential chain pinned.

    Region resolution matches get_bedrock_runtime_client(): the dedicated setting when supplied,
    standard boto3 resolution otherwise.

    These two settings bound the instance-metadata provider only; they are not an overall deadline
    for get_credentials(). The other providers that do work during resolution carry their own,
    separate limits: the ECS/EKS container fetcher uses botocore's fixed ContainerMetadataFetcher
    values (2s per attempt, 3 attempts), and a profile configured with credential_process runs an
    external command that botocore does not time out at all. Assume-role and web-identity providers
    return deferred credentials, so no STS call happens here.
    """
    botocore_session = botocore.session.Session()
    botocore_session.set_config_variable("metadata_service_timeout", _METADATA_TIMEOUT_SECONDS)
    botocore_session.set_config_variable("metadata_service_num_attempts", _METADATA_NUM_ATTEMPTS)
    return boto3.Session(botocore_session=botocore_session, region_name=region or None)


def _check_aws_bedrock_configured() -> ModelProviderReadiness:
    """Validate the Bedrock region and resolve the standard AWS credential chain."""
    session = _bedrock_session(config.AWS_BEDROCK_REGION.strip())

    missing = []

    if not session.region_name:
        missing.append(MISSING_AWS_REGION)

    try:
        credentials = session.get_credentials()
    except Exception:
        # The chain failed to resolve rather than resolving to nothing, so whether credentials
        # exist is unknown. Report that instead of asserting they are absent.
        logger.exception("AWS credential chain resolution failed during model provider readiness check")
        return ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CHECKED,
            provider=LLMProvider.AWS_BEDROCK.value,
            missing=[*missing, UNRESOLVED_AWS_CREDENTIALS],
        )

    if credentials is None:
        missing.append(MISSING_AWS_CREDENTIALS)

    return ModelProviderReadiness(
        status=ModelProviderStatus.NOT_CONFIGURED if missing else ModelProviderStatus.CONFIGURED,
        provider=LLMProvider.AWS_BEDROCK.value,
        missing=missing,
    )


def check_model_provider_readiness() -> ModelProviderReadiness:
    """Validate Azure/Bedrock configuration without changing provider selection."""
    if is_litellm_enabled() and config.LLM_PROXY_MODE == "lite_llm":
        return ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CHECKED,
            provider="litellm_proxy",
            missing=[],
        )

    default_model = llm_service.get_default_model_for_category(ModelCategory.GLOBAL)
    if default_model is None:
        logger.warning(f"No enabled global default model for MODELS_ENV={config.MODELS_ENV}")
        return ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CONFIGURED,
            provider=None,
            missing=[MISSING_GLOBAL_DEFAULT_MODEL],
        )

    provider = default_model.provider

    if provider == LLMProvider.AZURE_OPENAI:
        missing = []
        if not _is_present(config.AZURE_OPENAI_API_KEY):
            missing.append("AZURE_OPENAI_API_KEY")
        if not _is_present(config.AZURE_OPENAI_URL):
            missing.append("AZURE_OPENAI_URL")
        return ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CONFIGURED if missing else ModelProviderStatus.CONFIGURED,
            provider=provider.value,
            missing=missing,
        )

    if provider == LLMProvider.AWS_BEDROCK:
        return _check_aws_bedrock_configured()

    if provider is None:
        logger.warning(f"Model provider unset for default model '{default_model.base_name}'")
        return ModelProviderReadiness(
            status=ModelProviderStatus.NOT_CONFIGURED,
            provider=None,
            missing=[MISSING_MODEL_PROVIDER],
        )

    return ModelProviderReadiness(
        status=ModelProviderStatus.NOT_CHECKED,
        provider=provider.value,
        missing=[],
    )


def log_model_provider_readiness(result: ModelProviderReadiness) -> None:
    """Log the readiness result once at startup. Never logs setting values, only names."""

    if result.status == ModelProviderStatus.CONFIGURED:
        logger.info(
            "Model provider readiness: configured. Provider=%s. MODELS_ENV=%s.",
            result.provider,
            config.MODELS_ENV,
        )

    elif result.status == ModelProviderStatus.NOT_CONFIGURED:
        message = (
            "Model provider readiness: NOT configured. Missing: %s. "
            "MODELS_ENV=%s. The first chat request will fail until this is resolved."
        )
        logger.warning(
            message,
            ", ".join(result.missing),
            config.MODELS_ENV,
        )

    elif result.missing:
        message = "Model provider readiness: not checked. Reason: %s. MODELS_ENV=%s. Runtime behavior is unchanged."
        logger.warning(
            message,
            ", ".join(result.missing),
            config.MODELS_ENV,
        )

    else:
        message = "Model provider readiness: not checked for provider=%s. MODELS_ENV=%s. Runtime behavior is unchanged."
        logger.info(
            message,
            result.provider,
            config.MODELS_ENV,
        )
