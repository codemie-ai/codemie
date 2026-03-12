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

import json
from json import JSONDecodeError
from typing import Any, Dict, Type, Union

import requests
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.code.models import SonarConfig
from codemie_tools.code.sonar.tools_vars import SONAR_TOOL


class SonarToolInput(BaseModel):
    relative_url: str = Field(
        ...,
        description="""
        Required parameter: The relative URI for SONAR REST API.
        URI must start with a forward slash and '/api/issues/search..'.
        Do not include query parameters in the URL, they must be provided separately in 'params'.
        For search/read operations, you MUST always get "cleanCodeAttributeCategories",
        "severity", "issueStatuses", "types" and set maxResult,
        until users ask explicitly for more fields.
        """,
    )
    params: Union[str, Dict[str, Any], None] = Field(
        default=None,
        description="""
        Optional parameters to be sent in request body or query params.

        RECOMMENDED: Provide as a dictionary (dict) - this avoids JSON escaping issues.
        LEGACY: Can also accept a JSON string, but this is error-prone for complex content.

        For search/read operations, you MUST always get "cleanCodeAttributeCategories",
        "severity", "issueStatuses", "types" and set maxResult,
        until users ask explicitly for more fields.

        Dict format examples (RECOMMENDED):
        - Simple query: params={"severity": "MAJOR,CRITICAL", "types": "BUG,VULNERABILITY"}
        - With pagination: params={"severity": "MAJOR", "ps": 100, "p": 1}

        JSON string format (LEGACY - only use if dict not available):
        - Must properly escape all quotes and special characters
        """,
    )


def parse_payload_params(params: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
    """
    Parse Sonar query parameters.

    Accepts either a dictionary (recommended) or a JSON string (legacy).
    Using a dictionary is preferred as it avoids JSON escaping issues.

    Args:
        params: Dictionary of parameters (recommended) or JSON string (legacy), or None

    Returns:
        Dictionary representation of the parameters

    Raises:
        ToolException: If the params format is invalid
    """
    if not params:
        return {}

    # Handle dict directly (no serialization/parsing round-trip needed)
    if isinstance(params, dict):
        return params

    # Handle string for backward compatibility
    if isinstance(params, str):
        try:
            return json.loads(params)
        except JSONDecodeError as e:
            error_detail = f"at line {e.lineno}, column {e.colno}" if hasattr(e, "lineno") else ""

            # Sanitize params for logging (truncate long strings)
            params_preview = params[:200] + "..." if len(params) > 200 else params

            raise ToolException(
                f"Sonar tool exception. Passed 'params' string is not valid JSON.\n"
                f"Error: {e.msg} {error_detail}\n"
                f"Preview: {params_preview}\n"
                f"Tip: Use dict format instead of JSON string to avoid escaping issues.\n"
                f"Example: params={{'severity': 'MAJOR', 'types': 'BUG'}}\n"
                f"Please correct and send again."
            )

    raise ToolException(
        f"Invalid params type: {type(params).__name__}. " f"Expected dict (recommended), str (legacy), or None."
    )


class SonarTool(CodeMieTool):
    name: str = SONAR_TOOL.name
    config: SonarConfig
    args_schema: Type[BaseModel] = SonarToolInput
    description: str = SONAR_TOOL.description

    def _healthcheck(self):
        """Performs a healthcheck for Sonar integration.

        Validates the provided token and verifies that the specified
        project is accessible in SonarQube.
        """
        # Validate token
        response = self.execute("api/authentication/validate", "")

        if not response.get("valid", False):
            raise ToolException("Invalid token")

        # Validate project
        if not self.config.sonar_project_name:
            raise ToolException("Project name not provided")

        response = self.execute("api/components/show", f'{{"component": "{self.config.sonar_project_name}"}}')

        if "component" not in response:
            errors = response.get("errors", [{"msg": "Error occurred when trying to get project information"}])
            error_msg = (" | ").join([error.get("msg", "") for error in errors])
            raise ToolException(error_msg)

        component = response.get("component", {})

        if component.get("key", "") == self.config.sonar_project_name and component.get("qualifier", "") == "TRK":
            return  # Success

        raise ToolException("Project not found or invalid qualifier")

    def execute(self, relative_url: str, params: str, *args) -> Any:
        payload_params = parse_payload_params(params)
        payload_params["componentKeys"] = self.config.sonar_project_name
        return requests.get(
            url=f"{self.config.url}/{relative_url}",
            auth=(self.config.token, ""),
            params=payload_params,
        ).json()
