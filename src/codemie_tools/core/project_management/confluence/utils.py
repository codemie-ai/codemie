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
import logging
from json import JSONDecodeError
from typing import Dict, Any, Union

from atlassian import Confluence
from langchain_core.tools import ToolException
from markdown import markdown

try:
    # Try using the direct imports first (for production)
    from codemie_tools.base.utils import clean_json_string
except ModuleNotFoundError:
    # Fall back to relative imports (for tests)
    from src.codemie_tools.base.utils import clean_json_string

logger = logging.getLogger(__name__)


def validate_creds(confluence: Confluence):
    if confluence.url is None or confluence.url == "":
        logger.error("Confluence URL is required. Seems there no Confluence credentials provided.")
        raise ToolException("Confluence URL is required. You should provide Confluence credentials in 'Integrations'.")


def prepare_page_payload(payload: dict) -> dict:
    """Convert Confluence payload from Markdown to HTML format for body.storage.value field."""
    if value := payload.get("body", {}).get("storage", {}).get("value"):
        payload["body"]["storage"]["value"] = markdown(value)  # convert markdown to HTML

    return payload


def parse_payload_params(params: Union[str, Dict[str, Any], None]) -> Dict[str, Any]:
    """
    Parse Confluence payload parameters.

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
            logger.error(f"Confluence tool: JSON parsing failed {error_detail}: {e.msg}")

            # Sanitize params for logging (truncate long strings)
            params_preview = params[:200] + "..." if len(params) > 200 else params

            raise ToolException(
                f"Confluence tool exception. Passed 'params' string is not valid JSON.\n"
                f"Error: {e.msg} {error_detail}\n"
                f"Preview: {params_preview}\n"
                f"Tip: Use dict format instead of JSON string to avoid escaping issues.\n"
                f"Example: params={{'title': 'Page Title', 'content': 'Multi-line\\ntext'}}\n"
                f"Please correct and send again."
            )

    raise ToolException(
        f"Invalid params type: {type(params).__name__}. " f"Expected dict (recommended), str (legacy), or None."
    )
