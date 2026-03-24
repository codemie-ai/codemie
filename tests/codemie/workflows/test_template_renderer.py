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

import pytest
from unittest.mock import patch
from jinja2 import TemplateSyntaxError

from codemie.workflows.jinja_template_renderer import TemplateRenderer


def test_default_summary():
    """Test summary rendering with default template."""
    json_str_list = [
        '{"name": "John Doe", "age": 30, "email": "john@example.com"}',
        '{"name": "Jane Smith", "age": 25, "email": "jane@example.com"}',
    ]

    renderer = TemplateRenderer()
    result = renderer.render_template_batch(json_str_list)

    assert "John Doe" in result
    assert "jane@example.com" in result


def test_custom_template():
    """Test summary rendering with custom template."""
    json_str_list = [
        '{"name": "John Doe", "age": 30, "email": "john@example.com"}',
        '{"name": "Jane Smith", "age": 25, "email": "jane@example.com"}',
    ]

    custom_template = """# User List
{% for item in items %}
User {{ loop.index }}:
  Name: {{ item.name }}
  Age: {{ item.age }}
  Contact: {{ item.email }}
{% endfor %}"""

    renderer = TemplateRenderer()
    result = renderer.render_template_batch(json_str_list, custom_template)

    assert "User 1:" in result
    assert "User 2:" in result
    assert "John Doe" in result


def test_table_format_template():
    """Test summary rendering with table format template."""
    json_str_list = [
        '{"name": "John Doe", "age": 30, "email": "john@example.com"}',
        '{"name": "Jane Smith", "age": 25, "email": "jane@example.com"}',
    ]

    table_template = """# User Table
| Name | Age | Email |
|------|-----|-------|
{% for item in items %}
| {{ item.name }} | {{ item.age }} | {{ item.email }} |
{%- endfor %}"""

    renderer = TemplateRenderer()
    result = renderer.render_template_batch(json_str_list, table_template)

    assert "| Name | Age | Email |" in result
    assert "| John Doe |" in result


def test_empty_and_invalid_input():
    """Test handling of empty and invalid input."""
    renderer = TemplateRenderer()

    # Test empty list
    assert renderer.render_template_batch([]) == ""

    # Test list with empty strings
    assert renderer.render_template_batch(["", "  "]) == ""

    # Test invalid JSON
    result = renderer.render_template_batch(['{"name": "Invalid JSON"}', '{"invalid": '])
    assert "Invalid JSON" in result


def test_custom_nested_template():
    """Test summary rendering with nested data structure."""
    json_str_list = [
        '''
        {
            "user": {
                "name": "John Doe",
                "contacts": {
                    "email": "john@example.com",
                    "phone": "123-456-7890"
                }
            },
            "roles": ["admin", "user"]
        }
        '''
    ]

    nested_template = """# Detailed User Summary
{% for item in items %}
User Details:
  Name: {{ item.user.name }}
  Contacts:
    Email: {{ item.user.contacts.email }}
    Phone: {{ item.user.contacts.phone }}
  Roles:
  {% for role in item.roles %}
    - {{ role }}
  {% endfor %}
{% endfor %}"""

    renderer = TemplateRenderer()
    result = renderer.render_template_batch(json_str_list, nested_template)

    assert "John Doe" in result
    assert "123-456-7890" in result
    assert "admin" in result


@patch('codemie.workflows.jinja_template_renderer.logger')
def test_possibly_incorrect_json_string(logger_mock):
    """Test logging for possibly incorrect JSON string."""
    renderer = TemplateRenderer()
    json_str_list = ['{}']  # Empty JSON object should trigger the log

    renderer.render_template_batch(json_str_list)

    logger_mock.debug.assert_any_call("Possibly incorrect json string extracted. JSON string={}")


@patch('codemie.workflows.jinja_template_renderer.logger')
def test_cannot_parse_json(logger_mock):
    """Test logging for unparseable JSON string."""
    renderer = TemplateRenderer()
    json_str_list = ['{"invalid": ']  # Invalid JSON should trigger the log

    renderer.render_template_batch(json_str_list)

    logger_mock.error.assert_any_call(
        "Cannot parse json while jinja template rendering. Skipping JSON string={\"invalid\": "
    )


@patch('codemie.workflows.jinja_template_renderer.logger')
def test_template_syntax_error(logger_mock):
    """Test logging and exception for template syntax error."""
    renderer = TemplateRenderer()
    expected_error = "unexpected '}'"
    invalid_template = "{% for item in items %}{{ item.name }{% endfor %}"  # Missing closing item.name tag }
    expected_log_error = f"Syntax error in {invalid_template}, error: {expected_error}"
    json_str_list = ['{"name": "John Doe", "age": 30, "email": "john@example.com"}']

    with pytest.raises(TemplateSyntaxError) as error_raised:
        renderer.render_template_batch(json_str_list, invalid_template)

    assert str(error_raised.value) == expected_error
    logger_mock.error.assert_called_once_with(expected_log_error)


def test_ssti_payload_in_custom_template_is_blocked():
    """SSTI attack payload passed as custom template must be blocked by the sandbox."""
    from codemie.core.template_security import TemplateSecurityError

    renderer = TemplateRenderer()
    malicious_template = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
    json_str_list = ['{"name": "John Doe"}']

    with pytest.raises(TemplateSecurityError):
        renderer.render_template_batch(json_str_list, malicious_template)


def test_values_with_special_chars_not_html_escaped():
    """Values with '&', '<', '>' must not be HTML-escaped — TemplateRenderer output is not HTML."""
    json_str_list = ['{"summary": "open & resolved", "range": "score <10 or >90"}']
    template = "{% for item in items %}{{ item.summary }} | {{ item.range }}{% endfor %}"

    renderer = TemplateRenderer()
    result = renderer.render_template_batch(json_str_list, template)

    assert "open & resolved" in result
    assert "score <10 or >90" in result
    assert "&amp;" not in result
    assert "&lt;" not in result
    assert "&gt;" not in result
