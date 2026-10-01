# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Shared guard: refuse OAuth flows when token-at-rest storage is not confidential.

OAuth refresh tokens are long-lived credentials. The `plain` and `base64` encryption
backends provide no confidentiality, so persisting tokens through them stores them in
effectively cleartext. When an OAuth provider is enabled with such a backend, block the
flow unless an operator explicitly opts in via OAUTH_ALLOW_INSECURE_TOKEN_STORAGE.
"""

from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.encryption.encryption_factory import EncryptionFactory, EncryptionType

_INSECURE_TYPES = {EncryptionType.PLAIN_TEXT, EncryptionType.BASE64_ENCRYPTION}

# Tool OAuth persists per-user tokens in the enterprise token vault; these are the enterprise
# API guarantees it relies on, and the first release that provides them.
MIN_ENTERPRISE_VERSION = "2.3.38"
_REQUIRED_TOKEN_FIELDS = ("provider_username", "provider_user_id", "provider_metadata")


def is_token_storage_secure() -> bool:
    """False when the active encryption backend is plain or base64 (non-confidential)."""
    return EncryptionFactory.get_current_encryption_service_type() not in _INSECURE_TYPES


def is_tool_oauth_enabled() -> bool:
    """True when any tool OAuth provider is enabled, i.e. tool tokens may be stored in the TMS."""
    return bool(config.GITLAB_OAUTH_ENABLED or config.JIRA_OAUTH_ENABLED or config.CONFLUENCE_OAUTH_ENABLED)


def assert_secure_token_storage(provider_label: str) -> None:
    """Raise 503 when token storage is insecure and the escape hatch is off; otherwise no-op."""
    if is_token_storage_secure() or config.OAUTH_ALLOW_INSECURE_TOKEN_STORAGE:
        return
    etype = EncryptionFactory.get_current_encryption_service_type().value
    raise ExtendedHTTPException(
        503,
        f"{provider_label} OAuth is disabled because tokens cannot be stored securely "
        f"(ENCRYPTION_TYPE={etype}). Configure a KMS/Vault backend, or set "
        f"OAUTH_ALLOW_INSECURE_TOKEN_STORAGE=true for local development only.",
    )


def warn_if_insecure_token_storage(provider_label: str) -> None:
    """Log a WARNING when a provider is enabled with insecure storage (regardless of the escape hatch)."""
    if not is_token_storage_secure():
        etype = EncryptionFactory.get_current_encryption_service_type().value
        logger.warning(
            f"{provider_label} OAuth is enabled but tokens are stored WITHOUT confidential "
            f"encryption (ENCRYPTION_TYPE={etype}). Refresh tokens are persisted insecurely."
        )


def warn_insecure_oauth_storage_for_enabled_providers() -> None:
    """Startup helper: warn once per enabled OAuth provider whose token storage is insecure."""
    if config.GITLAB_OAUTH_ENABLED:
        warn_if_insecure_token_storage("GitLab")
    if config.JIRA_OAUTH_ENABLED:
        warn_if_insecure_token_storage("Jira")
    if config.CONFLUENCE_OAUTH_ENABLED:
        warn_if_insecure_token_storage("Confluence")


def assert_oauth_state_signing_secret_configured() -> None:
    """Fail startup when tool OAuth is enabled without a strong state-signing secret.

    Tool OAuth signs its ``state`` and encrypts the PKCE/credential records with keys derived from
    ``MCP_AUTH_HMAC_SECRET`` (shared with MCP auth), so that secret must be strong when enabled.
    """
    oauth_enabled = config.GITLAB_OAUTH_ENABLED or config.JIRA_OAUTH_ENABLED or config.CONFLUENCE_OAUTH_ENABLED
    if not oauth_enabled:
        return
    secret = config.MCP_AUTH_HMAC_SECRET or ""
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError(
            "Tool OAuth (GitLab / Jira / Confluence) requires MCP_AUTH_HMAC_SECRET to be at least 32 "
            "bytes when a provider is enabled — it signs the OAuth state and encrypts the PKCE and "
            "credential records. Configure a strong secret and restart the application."
        )


def assert_token_vault_available() -> None:
    """Fail startup when tool OAuth is enabled without a compatible enterprise token vault.

    Tool OAuth stores per-user tokens in the enterprise Token Management System, so the
    `codemie-enterprise` package is required and must be new enough to carry the provider
    identity fields on the stored token, and TMS itself must be switched on. Checking at
    startup turns an obscure runtime TypeError or ImportError during a user's sign-in into
    an actionable boot failure.
    """
    oauth_enabled = config.GITLAB_OAUTH_ENABLED or config.JIRA_OAUTH_ENABLED or config.CONFLUENCE_OAUTH_ENABLED
    if not oauth_enabled:
        return
    try:
        from codemie_enterprise.mcp_auth import OAuth2TokenData
    except ImportError as exc:
        raise RuntimeError(
            "Tool OAuth (GitLab / Jira / Confluence) requires the codemie-enterprise package "
            f"({MIN_ENTERPRISE_VERSION} or newer) because per-user tokens are stored in the enterprise "
            "token vault. Install the enterprise extra or disable the OAuth providers."
        ) from exc

    missing = [field for field in _REQUIRED_TOKEN_FIELDS if field not in OAuth2TokenData.model_fields]
    if missing:
        raise RuntimeError(
            f"Installed codemie-enterprise is too old for tool OAuth: OAuth2TokenData is missing {missing}. "
            f"Upgrade to codemie-enterprise {MIN_ENTERPRISE_VERSION} or newer."
        )

    from codemie.service.oauth.token_port import ToolOAuthTokenPort

    ToolOAuthTokenPort.assert_configured()
