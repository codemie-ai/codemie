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
import re
import urllib.parse
from json import JSONDecodeError
from typing import Type, Any, Dict, Union

import requests
from langchain_core.tools import ToolException

from codemie_tools.base.codemie_tool import CodeMieTool
from .models import ServiceNowInput, ServiceNowConfig
from .tools_vars import SNOW_TABLE_TOOL


def clean_json_string(json_string):
    """
    Extract JSON object from a string, removing extra characters before '{' and after '}'.

    Args:
    json_string (str): Input string containing a JSON object.

    Returns:
    str: Cleaned JSON string or original string if no JSON object found.
    """
    pattern = r"^[^{]*({.*})[^}]*$"
    match = re.search(pattern, json_string, re.DOTALL)
    if match:
        return match.group(1)
    return json_string


def normalize_query_params(params: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
    """
    Parse ServiceNow query parameters.

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
            return json.loads(clean_json_string(params))
        except JSONDecodeError as e:
            error_detail = f"at line {e.lineno}, column {e.colno}" if hasattr(e, "lineno") else ""

            # Sanitize params for logging (truncate long strings)
            params_preview = params[:200] + "..." if len(params) > 200 else params

            raise ToolException(
                f"ServiceNow tool exception. Passed 'params' string is not valid JSON.\n"
                f"Error: {e.msg} {error_detail}\n"
                f"Preview: {params_preview}\n"
                f"Tip: Use dict format instead of JSON string to avoid escaping issues.\n"
                f"Example: params={{'sysparm_limit': 10, 'sysparm_query': 'active=true'}}\n"
                f"Please correct and send again."
            )

    raise ToolException(
        f"Invalid params type: {type(params).__name__}. " f"Expected dict (recommended), str (legacy), or None."
    )


def normalize_string(table: str) -> str:
    return table.replace('"', "").replace("'", "").replace("`", "").strip().lower()


class ServiceNowTableTool(CodeMieTool):
    args_schema: Type[ServiceNowInput] = ServiceNowInput
    name: str = SNOW_TABLE_TOOL.name
    description: str = SNOW_TABLE_TOOL.description
    config: ServiceNowConfig

    def _healthcheck(self):
        """Performs a healthcheck by querying a single incident record"""
        self.execute(method="GET", table="incident", params='{"sysparm_limit": 1}')

    def execute(self, method: str, table: str, sys_id: str = "", params: str = "", body: str = "") -> Any:
        query_params = normalize_query_params(params)
        method = normalize_string(method).upper()
        headers = {"x-sn-apikey": self.config.api_key}

        url = urllib.parse.urljoin(self.config.url, "/api/now/table/")
        url += normalize_string(table)

        if sys_id:
            url += f"/{normalize_string(sys_id)}"

        request_args = {"method": method, "url": url, "headers": headers}

        if query_params:
            request_args["params"] = query_params

        if body:
            request_args["json"] = json.loads(body)
        response = requests.request(**request_args)

        if response.status_code >= 300:
            raise ToolException(f"ServiceNow tool exception. Status: {response.status_code}. Response: {response.text}")

        return response.text
