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
from typing import List

from jinja2 import Template, TemplateSyntaxError

from codemie.configs import logger

# Default template for summary if none provided
DEFAULT_TEMPLATE = """# Summary
{% for item in items %}
{%- for key, value in item.items() %}
{{ key }}: {{ value }}
{%- endfor %}
{% endfor %}"""


class TemplateRenderer:
    """Class for rendering Jinja2 templates with JSON data."""

    @staticmethod
    def render_template_batch(json_str_list: List[str], template_str: str = None) -> str:
        """
        Render a summary template with multiple JSON data strings.
        Uses provided template or falls back to default if none provided.

        Args:
            json_str_list (List[str]): List of JSON strings to be summarized
            template_str (str, optional): Custom template string. Defaults to None.

        Returns:
            str: Rendered summary template

        Raises:
            Exception: If JSON parsing errors occur
        """
        # Parse all JSON strings into a list of dictionaries
        json_data_list = []

        for json_str in json_str_list:
            if not json_str:
                continue
            try:
                json_data = {}
                if isinstance(json_str, dict):
                    json_data = json_str
                elif isinstance(json_str, str):
                    json_data = json.loads(json_str.strip())

                if len(json_data) > 0:
                    json_data_list.append(json_data)
                else:
                    logger.debug(f"Possibly incorrect json string extracted. JSON string={json_str}")
                logger.debug(f"Render template batch item. JSON={json_data}")

            except json.JSONDecodeError:
                # Skip item if it cannot be parsed or it's invalid json
                logger.error(f"Cannot parse json while jinja template rendering. Skipping JSON string={json_str}")
                continue

        if not json_data_list:
            return ""

        try:
            # Use provided template or default
            template = template_str if template_str is not None else DEFAULT_TEMPLATE
            logger.debug(f"Using template: {template}.")
            template_object = Template(template)
        except TemplateSyntaxError as e:
            logger.error(f"Syntax error in {template}, error: {e}")
            raise e

        # Render template with all JSON data
        rendered_content = template_object.render(items=json_data_list)

        return rendered_content
