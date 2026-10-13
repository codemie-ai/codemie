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

from __future__ import annotations

from typing import TYPE_CHECKING

from codemie.rest_api.models.assistant import InlineCredential
from codemie.rest_api.models.settings import SettingType
from codemie.service.assistant.sensitive_prompt_variables import get_masked_prompt_variables

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant, MCPServerDetails, ToolKitDetails


def validate_assistant_inline_integrations(assistant: Assistant) -> dict[str, object]:
    """
    Validates if an assistant contains inline integration credentials.
    Note: Prompt variables are now returned separately, not as inline credentials.
    Returns a dict with validation result and details.
    """
    inline_credentials = []

    # Process toolkit credentials
    inline_credentials.extend(_check_toolkit_credentials(assistant.toolkits))

    # Process MCP server credentials
    inline_credentials.extend(_check_mcp_server_credentials(assistant.mcp_servers))

    # Build result with separate prompt_variables field
    result = _build_validation_result(inline_credentials)

    # Add prompt variables separately (not as inline credentials)
    # Mask sensitive variable values for security
    if assistant.prompt_variables:
        result["prompt_variables"] = get_masked_prompt_variables(assistant.prompt_variables)

    return result


def _check_toolkit_credentials(toolkits: list[ToolKitDetails]) -> list[InlineCredential]:
    """
    Check for inline credentials in toolkits and their tools.
    Returns a list of found inline credentials.
    """
    credentials = []

    for toolkit in toolkits:
        # Check toolkit level credentials
        credentials.extend(
            _check_object_credential_values(
                toolkit, "toolkit_settings", toolkit_name=toolkit.toolkit, label=toolkit.label
            )
        )

        # Check tool level credentials
        for tool in toolkit.tools:
            credentials.extend(
                _check_object_credential_values(
                    tool,
                    "tool_settings",
                    toolkit_name=toolkit.toolkit,
                    tool_name=tool.name,
                    label=getattr(tool, "label", tool.name),  # Use label if exists, otherwise name
                )
            )

    return credentials


def _check_object_credential_values(obj: object, credential_type: str, **kwargs: object) -> list[InlineCredential]:
    """
    Generic helper to check if an object has credential values.
    Returns list with one credential if found, empty list otherwise.
    """
    if not hasattr(obj, "settings") or not obj.settings:
        return []

    if not hasattr(obj.settings, "credential_values") or not obj.settings.credential_values:
        return []

    # Create InlineCredential with dynamic parameters
    cred_params = {"credential_type": credential_type}
    cred_params.update(kwargs)

    # Map parameter names to InlineCredential field names
    param_mapping = {
        "toolkit_name": "toolkit",
        "tool_name": "tool",
        "mcp_server_name": "mcp_server",
        "label": "label",
        "env_vars": "env_vars",
        "credential_type": "credential_type",
    }

    # Adjust parameter names to match InlineCredential fields
    mapped_params = {param_mapping.get(k, k): v for k, v in cred_params.items() if k in param_mapping}

    return [InlineCredential(**mapped_params)]


def _check_mcp_server_credentials(mcp_servers: list[MCPServerDetails]) -> list[InlineCredential]:
    """
    Check for inline credentials in MCP servers.
    Returns a list of found inline credentials.
    """
    credentials = []

    for server in mcp_servers:
        # Check environment variables
        env_var_credentials = _check_object_credential_values(
            server, "mcp_environment_vars", mcp_server_name=server.name, toolkit_name="MCP"
        )
        credentials.extend(env_var_credentials)

        # A server pinned to a personal (USER) integration reuses the author's own credentials
        # for every marketplace consumer. When the reference carries no inlined credential_values
        # the env-vars check above misses it, so surface it here for the Credential Review step.
        pinned_settings = getattr(server, "settings", None)
        if (
            not env_var_credentials
            and pinned_settings is not None
            and getattr(pinned_settings, "setting_type", None) == SettingType.USER
        ):
            credentials.append(
                InlineCredential(
                    mcp_server=server.name,
                    credential_type=getattr(pinned_settings.credential_type, "value", pinned_settings.credential_type),
                    integration_alias=getattr(pinned_settings, "alias", None),
                    toolkit="MCP",
                )
            )

        # Check auth token
        if (
            server.mcp_connect_auth_token
            and hasattr(server.mcp_connect_auth_token, "credential_values")
            and server.mcp_connect_auth_token.credential_values
        ):
            credentials.append(
                InlineCredential(mcp_server=server.name, credential_type="mcp_auth_token", toolkit="MCP")
            )

        # Check inline config env vars
        if server.config and server.config.env and len(server.config.env) > 0:
            credentials.append(
                InlineCredential(
                    mcp_server=server.name,
                    credential_type="mcp_inline_config_env",
                    env_vars=list(server.config.env.keys()),
                    toolkit="MCP",
                )
            )

    return credentials


def _build_validation_result(inline_credentials: list[InlineCredential]) -> dict[str, object]:
    """
    Build the final validation result based on found credentials.
    """
    if inline_credentials:
        return {
            "is_valid": False,
            "message": "This assistant contains inline integration credentials that will be shared with all "
            "users who use this assistant. Please confirm that you want to publish it.",
            "inline_credentials": inline_credentials,
        }

    return {"is_valid": True, "inline_credentials": []}
