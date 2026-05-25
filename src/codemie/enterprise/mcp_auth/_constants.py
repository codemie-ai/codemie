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

from __future__ import annotations

from datetime import timedelta

from codemie.core.utils import get_api_root_path

SUPPORTED_AUTH_TYPES = ("oauth2", "saml")
_REQUIRED_AUTH_FIELDS = {
    "oauth2": ("authorization_url", "token_url", "client_id", "client_type", "scopes", "token_delivery"),
    "saml": (
        "sso_url",
        "entity_id",
        "idp_entity_id",
        "idp_x509cert",
        "saml_credential_attribute",
        "saml_session_ttl",
        "token_delivery",
    ),
}
_HTTPS_ONLY_FIELDS = ("authorization_url", "token_url", "sso_url")
_SAML_HTTP_ERROR = "SAML is not supported for HTTP transport. Use OAuth2 for HTTP MCP servers"
_DISCOVERED_AUTH_CONFIG_ID_PREFIX = "discovered:"
_RESERVED_DISCOVERED_AUTH_CONFIG_ID_ERROR = "auth_config.id cannot use reserved 'discovered:' prefix"

_OAUTH2_CALLBACK_PATH = "/v1/mcp-auth/oauth2/callback"
_CLIENT_METADATA_DOCUMENT_PATH = "/oauth/client-metadata.json"
_CLIENT_METADATA_CACHE_CONTROL = "max-age=3600"
_OAUTH2_CALLBACK_PAGE_SCRIPT_PATH = get_api_root_path() + "/v1/mcp-auth/oauth2/callback-page.js"
_SAML_ACS_PATH = "/v1/mcp-auth/saml/acs"
_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}
_INVALID_OAUTH2_CONFIG_MESSAGE = "Invalid OAuth2 MCP configuration"
_INVALID_MCP_AUTH_CONFIG_MESSAGE = "Invalid MCP auth configuration"
_INVALID_MCP_SERVER_URL_MESSAGE = "Invalid MCP server URL for OAuth2 initiation"
_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE = "MCP auth service is not initialized"
_MCP_AUTH_TEMPORARILY_UNAVAILABLE = "MCP auth temporarily unavailable"
_MCP_AUTH_RETRY_AFTER_INIT_HELP = "Try again after the MCP auth service finishes initializing."
_MCP_AUTH_REDIS_RETRY_HELP = "Retry after Redis connectivity is restored."
_INSTALL_ENTERPRISE_MCP_AUTH_HELP = "Install the enterprise MCP auth package and retry."
_AUTHENTICATION_FAILED_TITLE = "Authentication failed"
_SP_METADATA_SAML_ONLY_MESSAGE = "SP metadata is only available for SAML auth configurations"
_SP_METADATA_GENERATION_FAILED_MESSAGE = "SP metadata generation failed"
_CALLBACK_CONTENT_SECURITY_POLICY = "default-src 'none'; script-src 'self'"
_CALLBACK_SECURITY_HEADERS = {
    "Content-Security-Policy": _CALLBACK_CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
}
_CALLBACK_SUCCESS_MESSAGE = "Authentication complete. Return to CodeMie to continue using the MCP server."
_CALLBACK_TRANSITION_MESSAGE = "Completing authentication..."
_CALLBACK_SUCCESS_CLOSE_MESSAGE = "Authentication successful! You can close this tab."
_CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE = "Authentication successful! Open CodeMie to continue."
_CALLBACK_VERIFICATION_FAILURE_MESSAGE = (
    "Authentication session could not be verified. Return to CodeMie and try again."
)
_CALLBACK_EXPIRED_MESSAGE = "Authentication session expired. Return to CodeMie and try again."
_CALLBACK_REDIS_UNAVAILABLE_MESSAGE = (
    "Authentication session could not be verified. Return to CodeMie and try again when the service is available."
)
_CALLBACK_CONFIG_ERROR_MESSAGE = (
    "Authentication could not be completed because the MCP server configuration is invalid. "
    "Contact your administrator if the problem persists."
)
_CALLBACK_RUNTIME_ERROR_MESSAGE = "Authentication could not be completed. Return to CodeMie and try again."
_CALLBACK_TMS_STORE_ERROR_MESSAGE = (
    "Authentication succeeded but credentials could not be saved. Return to CodeMie and try again."
)
_CALLBACK_RECOVERY_TEXT = "Return to CodeMie and try again."
_CALLBACK_CONTACT_ADMIN_TEXT = "Contact your administrator if the problem persists."
_CALLBACK_STATE_MAX_AGE = timedelta(minutes=10)
_CALLBACK_EVENT_TYPE = "mcp_auth_callback"
_CALLBACK_ERROR_SESSION_EXPIRED = "session_expired"
_CALLBACK_ERROR_VERIFICATION_FAILED = "verification_failed"
_CALLBACK_ERROR_CONFIGURATION = "configuration_error"
_CALLBACK_ERROR_CREDENTIALS_STORE_FAILED = "credentials_store_failed"
_CALLBACK_ERROR_RUNTIME = "runtime_error"
_CALLBACK_FALLBACK_DELAY_MS = 300

MCP_AUTH_TRUSTED_AS_DOMAINS_KEY = "MCP_AUTH_TRUSTED_AS_DOMAINS"
MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST = "MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST"
MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH = 10000

DISCOVERY_BRIDGE_UNAVAILABLE_FAILURE_REASON = "discovery_bridge_unavailable"

_DISCOVERED_AUTH_RECOVERY_ACTION = "Configure auth_config with pre-registered credentials for this server"

_POST_AUTH_401_REFRESH_FAILURE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "token_not_found": ("Stored credential missing for server.", "warning"),
    "reauth_required_expired": ("Refresh token expired, revoked, or requires user action.", "warning"),
    "reauth_required_crypto": ("Stored credential cannot be decrypted or has invalid payload.", "warning"),
    "tms_unavailable": ("Credential refresh temporarily unavailable.", "error"),
    "tms_persistence_error": ("Credential refresh could not be persisted.", "error"),
    "tms_audit_persistence_error": ("Credential refresh audit could not be persisted.", "error"),
    "token_refresh_error": ("OAuth2 refresh returned an unexpected failure.", "error"),
}
