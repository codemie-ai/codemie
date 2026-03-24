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

"""
Security tests for template rendering to prevent Server-Side Template Injection (SSTI) attacks.

These tests verify that the secure template rendering utilities properly block
malicious template injection attempts while allowing legitimate template usage.
"""

import pytest

from codemie.core.template_security import (
    RestrictedSandboxEnvironment,
    TemplateSecurityError,
    render_secure_template,
    render_system_prompt_template,
    sanitize_template_context,
    validate_template_string,
)


# Known SSTI attack payloads from real-world exploits
SSTI_ATTACK_PAYLOADS = [
    # Access to __class__ and method resolution order
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ [].__class__.__base__.__subclasses__() }}",
    "{{ {}.__class__.__bases__[0].__subclasses__() }}",
    # Access to __globals__ for module imports
    "{{ ''.__class__.__mro__[2].__subclasses__()[40].__init__.__globals__ }}",
    "{{ config.__class__.__init__.__globals__ }}",
    # OS command execution attempts
    "{{ ''.__class__.__mro__[1].__subclasses__()[104].__init__.__globals__['sys'].modules['os'].popen('whoami').read() }}",
    "{{ ''.__class__.__bases__[0].__subclasses__()[104].__init__.__globals__['sys'].modules['subprocess'].check_output(['id']) }}",
    # Eval/exec attempts
    "{{ ''.__class__.__mro__[1].__subclasses__()[104].__init__.__globals__['__builtins__']['eval']('__import__(\"os\").system(\"ls\")') }}",
    "{% for x in ''.__class__.__mro__[1].__subclasses__() %}{% if x.__name__ == 'Popen' %}{{ x('whoami') }}{% endif %}{% endfor %}",
    # __import__ usage
    "{{ __import__('os').popen('cat /etc/passwd').read() }}",
    # Access to func_globals
    "{{ func.__globals__ }}",
    # File access attempts
    "{{ open('/etc/passwd').read() }}",
    "{{ ''.__class__.__mro__[2].__subclasses__()[40]('/etc/passwd').read() }}",
    # Attribute filter exploitation
    "{{ ''|attr('__class__') }}",
]

# Legitimate templates that should work
LEGITIMATE_TEMPLATES = [
    ("Hello {{ name }}!", {"name": "World"}, "Hello World!"),
    ("Current user: {{ current_user }}", {"current_user": "john_doe"}, "Current user: john_doe"),
    ("Date: {{ date }}", {"date": "2025-01-15"}, "Date: 2025-01-15"),
    ("{{ greeting }}, {{ name }}!", {"greeting": "Hi", "name": "Alice"}, "Hi, Alice!"),
    ("Count: {{ items|length }}", {"items": [1, 2, 3]}, "Count: 3"),
    ("{% if active %}Active{% else %}Inactive{% endif %}", {"active": True}, "Active"),
    ("{% for item in items %}{{ item }}{% endfor %}", {"items": ["a", "b", "c"]}, "abc"),
]


class TestTemplateValidation:
    """Test template string validation for attack patterns."""

    @pytest.mark.parametrize("payload", SSTI_ATTACK_PAYLOADS)
    def test_validate_template_blocks_ssti_payloads(self, payload):
        """Ensure SSTI attack payloads are detected and blocked."""
        with pytest.raises(TemplateSecurityError) as exc_info:
            validate_template_string(payload, context_name="test_template")

        error_message = str(exc_info.value)
        assert "forbidden pattern" in error_message.lower() or "code injection" in error_message.lower()

    def test_validate_template_allows_legitimate_templates(self):
        """Ensure legitimate templates pass validation."""
        for template, _, _ in LEGITIMATE_TEMPLATES:
            # Should not raise any exception
            validate_template_string(template, context_name="legitimate_template")

    def test_validate_non_string_template(self):
        """Non-string templates should be handled gracefully."""
        # Should not raise exception
        validate_template_string(None, context_name="none_template")
        validate_template_string(123, context_name="int_template")
        validate_template_string([], context_name="list_template")


class TestContextSanitization:
    """Test template context sanitization."""

    def test_sanitize_removes_private_keys(self):
        """Private keys (starting with underscore) should be removed."""
        context = {"public_key": "value", "_private_key": "secret", "__dunder__": "dangerous"}

        sanitized = sanitize_template_context(context)

        assert "public_key" in sanitized
        assert "_private_key" not in sanitized
        assert "__dunder__" not in sanitized

    def test_sanitize_with_allowlist(self):
        """Only whitelisted keys should be included when allowlist is provided."""
        context = {"allowed1": "value1", "allowed2": "value2", "not_allowed": "value3"}

        allowed_keys = {"allowed1", "allowed2"}
        sanitized = sanitize_template_context(context, allowed_keys=allowed_keys)

        assert "allowed1" in sanitized
        assert "allowed2" in sanitized
        assert "not_allowed" not in sanitized

    def test_sanitize_blocks_dangerous_objects(self):
        """Module and class objects should be blocked."""
        import os
        import sys

        context = {"safe_string": "value", "os_module": os, "sys_module": sys}

        sanitized = sanitize_template_context(context)

        assert "safe_string" in sanitized
        assert "os_module" not in sanitized
        assert "sys_module" not in sanitized

    def test_sanitize_allows_safe_types(self):
        """Safe primitive types should be allowed."""
        context = {
            "string": "text",
            "integer": 42,
            "float": 3.14,
            "boolean": True,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
        }

        sanitized = sanitize_template_context(context)

        assert len(sanitized) == 6
        assert all(key in sanitized for key in context)

    def test_sanitize_blocks_invalid_keys(self):
        """Keys with spaces or special characters should be blocked."""
        context = {
            "valid_key": "any value with spaces is fine",
            "invalid key": "value",  # Space in key
            "invalid-key": "value",  # Hyphen in key
            "invalid@key": "value",  # Special char in key
        }

        sanitized = sanitize_template_context(context)

        assert "valid_key" in sanitized
        assert "invalid key" not in sanitized  # Blocked due to space in key
        assert "invalid-key" not in sanitized  # Blocked due to hyphen in key
        assert "invalid@key" not in sanitized  # Blocked due to @ in key

    def test_sanitize_allows_any_values(self):
        """String values can contain spaces, special characters, etc."""
        context = {
            "project_name": "My Project Name",  # Spaces in value are OK
            "user_email": "user@example.com",  # Special chars in value are OK
            "date": "2025-01-15",  # Hyphens in value are OK
        }

        sanitized = sanitize_template_context(context)

        assert "project_name" in sanitized
        assert sanitized["project_name"] == "My Project Name"
        assert "user_email" in sanitized
        assert sanitized["user_email"] == "user@example.com"
        assert "date" in sanitized
        assert sanitized["date"] == "2025-01-15"


class TestVariableKeyValidation:
    """Test variable key validation in templates."""

    def test_validate_key_alphanumeric_with_underscores(self):
        """Alphanumeric keys with underscores should be valid."""
        from codemie.core.template_security import validate_variable_key

        assert validate_variable_key("valid_key_123") is True
        assert validate_variable_key("AlphaNumeric_456") is True
        assert validate_variable_key("test_123_ABC") is True
        assert validate_variable_key("project_name") is True
        assert validate_variable_key("user_123") is True

    def test_validate_key_blocks_spaces(self):
        """Keys with spaces should be invalid."""
        from codemie.core.template_security import validate_variable_key

        assert validate_variable_key("hello world") is False
        assert validate_variable_key("test value") is False
        assert validate_variable_key(" leading_space") is False
        assert validate_variable_key("project name") is False

    def test_validate_key_blocks_special_characters(self):
        """Any special characters should be blocked from keys."""
        from codemie.core.template_security import validate_variable_key

        assert validate_variable_key("user@example.com") is False
        assert validate_variable_key("file.txt") is False
        assert validate_variable_key("path/to/file") is False
        assert validate_variable_key("value:123") is False
        assert validate_variable_key("func(arg)") is False
        assert validate_variable_key("value-name") is False
        assert validate_variable_key("my personal assistant") is False

    def test_template_with_invalid_variable_names(self):
        """Templates with invalid variable names should be rejected."""
        # Template with spaces in variable name
        with pytest.raises(TemplateSecurityError) as exc_info:
            validate_template_string("{{ my personal assistant }}", "test")
        assert "invalid variable" in str(exc_info.value).lower()

        # Template with hyphen in variable name
        with pytest.raises(TemplateSecurityError) as exc_info:
            validate_template_string("{{ project-name }}", "test")
        assert "invalid variable" in str(exc_info.value).lower()

        # Template with special characters in variable name
        with pytest.raises(TemplateSecurityError) as exc_info:
            validate_template_string("{{ user@email }}", "test")
        assert "invalid variable" in str(exc_info.value).lower()

    def test_template_with_valid_variable_names(self):
        """Templates with valid variable names should pass validation."""
        # Should not raise any exception
        validate_template_string("{{ project_name }}", "test")
        validate_template_string("{{ user_123 }}", "test")
        validate_template_string("{{ current_user }}", "test")
        validate_template_string("{{ date }}", "test")
        validate_template_string("{% for item in items %}{{ item }}{% endfor %}", "test")


class TestRestrictedSandboxEnvironment:
    """Test the custom sandboxed Jinja2 environment."""

    def test_sandbox_blocks_class_access(self):
        """Access to __class__ should be blocked."""
        env = RestrictedSandboxEnvironment()
        template = env.from_string("{{ ''.__class__ }}")

        with pytest.raises(TemplateSecurityError) as exc_info:
            template.render()

        assert "__class__" in str(exc_info.value)

    def test_sandbox_blocks_mro_access(self):
        """Access to __mro__ should be blocked."""
        env = RestrictedSandboxEnvironment()
        template = env.from_string("{{ ''.__class__.__mro__ }}")

        with pytest.raises(TemplateSecurityError):
            template.render()

    def test_sandbox_blocks_globals_access(self):
        """Access to __globals__ should be blocked."""
        env = RestrictedSandboxEnvironment()

        # Even if we pass a function, accessing __globals__ should fail
        def sample_func():
            pass

        template = env.from_string("{{ func.__globals__ }}")

        with pytest.raises(TemplateSecurityError):
            template.render({"func": sample_func})

    def test_sandbox_blocks_subclasses_access(self):
        """Access to __subclasses__ should be blocked."""
        env = RestrictedSandboxEnvironment()
        template = env.from_string("{{ ''.__class__.__base__.__subclasses__() }}")

        with pytest.raises(TemplateSecurityError):
            template.render()

    def test_sandbox_allows_legitimate_operations(self):
        """Legitimate template operations should work."""
        env = RestrictedSandboxEnvironment()

        # String operations
        template = env.from_string("{{ name.upper() }}")
        result = template.render({"name": "alice"})
        assert result == "ALICE"

        # List operations
        template = env.from_string("{{ items|length }}")
        result = template.render({"items": [1, 2, 3, 4, 5]})
        assert result == "5"

        # Dict operations
        template = env.from_string("{{ data.key }}")
        result = template.render({"data": {"key": "value"}})
        assert result == "value"

    def test_sandbox_autoescape_enabled(self):
        """HTML autoescaping should be enabled by default."""
        env = RestrictedSandboxEnvironment()
        template = env.from_string("{{ html }}")

        result = template.render({"html": "<script>alert('XSS')</script>"})

        # HTML should be escaped
        assert "&lt;script&gt;" in result
        assert "<script>" not in result


class TestSecureTemplateRendering:
    """Test the main secure template rendering function."""

    @pytest.mark.parametrize("payload", SSTI_ATTACK_PAYLOADS)
    def test_render_blocks_ssti_attempts(self, payload):
        """Secure rendering should block all SSTI attack attempts."""
        context = {"name": "test", "value": "data"}

        with pytest.raises(TemplateSecurityError):
            render_secure_template(payload, context, context_name="attack_test")

    @pytest.mark.parametrize("template,context,expected", LEGITIMATE_TEMPLATES)
    def test_render_allows_legitimate_templates(self, template, context, expected):
        """Legitimate templates should render correctly."""
        result = render_secure_template(template, context, context_name="legit_test")
        assert result == expected

    def test_render_with_allowlist(self):
        """Rendering with allowlist should only use allowed variables."""
        template = "{{ allowed }} {{ blocked }}"
        context = {"allowed": "YES", "blocked": "NO"}
        allowed_keys = {"allowed"}

        result = render_secure_template(
            template, context, context_name="allowlist_test", allowed_context_keys=allowed_keys
        )

        # 'allowed' should render, 'blocked' should be empty/undefined
        assert "YES" in result
        assert "NO" not in result

    def test_render_handles_syntax_errors(self):
        """Invalid Jinja2 syntax should be treated as plain text."""
        template = "{{ unclosed"  # Invalid Jinja2 syntax - no closing braces
        context = {}

        # Invalid syntax is treated as plain text, not an error
        result = render_secure_template(template, context, context_name="syntax_error_test")
        assert result == template  # Returned as-is

    def test_render_without_validation(self):
        """Rendering without validation should still use sandbox."""
        # Even without validation, sandboxing should prevent exploitation
        template = "{{ ''.__class__ }}"
        context = {}

        with pytest.raises(TemplateSecurityError):
            render_secure_template(template, context, validate_input=False)


class TestSystemPromptRendering:
    """Test system prompt specific rendering."""

    def test_system_prompt_with_standard_variables(self):
        """System prompt should render with standard variables."""
        template = "You are assisting {{ current_user }} on {{ date }}"
        context = {"current_user": "john_doe", "date": "2025-01-15"}

        result = render_system_prompt_template(template, context)

        assert "john_doe" in result
        assert "2025-01-15" in result

    def test_system_prompt_blocks_attacks(self):
        """System prompt rendering should block SSTI attacks."""
        malicious_template = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        context = {"current_user": "attacker"}

        with pytest.raises(TemplateSecurityError):
            render_system_prompt_template(malicious_template, context)

    def test_system_prompt_with_custom_variables_allowed(self):
        """Custom variables should be allowed when flag is True."""
        template = "{{ custom_var }}"
        context = {"custom_var": "custom_value"}

        result = render_system_prompt_template(template, context, allow_custom_variables=True)

        assert "custom_value" in result

    def test_system_prompt_with_custom_variables_disallowed(self):
        """Custom variables should be blocked when flag is False."""
        template = "{{ current_user }} {{ custom_var }}"
        context = {"current_user": "john", "custom_var": "should_be_blocked"}

        result = render_system_prompt_template(template, context, allow_custom_variables=False)

        assert "john" in result
        # custom_var should not render (will be empty/undefined)
        assert "should_be_blocked" not in result


class TestRealWorldScenarios:
    """Test real-world usage scenarios."""

    def test_user_controlled_system_prompt(self):
        """Test that user-controlled system prompts are safe."""
        # Simulate a user trying to inject malicious code via system prompt
        user_provided_prompt = "You are a helpful assistant. {{ ''.__class__.__mro__[1].__subclasses__() }}"
        context = {"current_user": "attacker"}

        with pytest.raises(TemplateSecurityError):
            render_system_prompt_template(user_provided_prompt, context)

    def test_assistant_with_prompt_variables(self):
        """Test assistant with custom prompt variables."""
        template = """You are {{ assistant_name }} helping {{ current_user }}.
Today is {{ date }}.
Project context: {{ project_name }}"""

        context = {
            "assistant_name": "CodeMie Bot",
            "current_user": "alice",
            "date": "2025-01-15",
            "project_name": "Project Alpha",
        }

        result = render_system_prompt_template(template, context, allow_custom_variables=True)

        assert "CodeMie Bot" in result
        assert "alice" in result
        assert "2025-01-15" in result
        assert "Project Alpha" in result

    def test_workflow_template_rendering(self):
        """Test workflow templates are rendered securely."""
        template = "Process {{ workflow_input }} for {{ user_id }}"
        context = {"workflow_input": "data analysis task", "user_id": "user123"}

        result = render_secure_template(template, context, context_name="workflow")

        assert "data analysis task" in result
        assert "user123" in result

    def test_html_escaping_in_prompts(self):
        """HTML in context should be properly escaped."""
        template = "Process: {{ description }}"
        context = {"description": "<script>alert('xss')</script>"}

        result = render_system_prompt_template(template, context)

        # HTML should be escaped
        assert "&lt;script&gt;" in result or "script" not in result.lower()

    def test_edge_case_empty_context(self):
        """Rendering with empty context should work."""
        template = "Hello World"
        context = {}

        result = render_secure_template(template, context)

        assert result == "Hello World"

    def test_edge_case_none_values(self):
        """None values in context should be handled gracefully."""
        template = "Value: {{ value }}"
        context = {"value": None}

        result = render_secure_template(template, context)

        # None should render as "None" (Jinja2 default behavior)
        assert result == "Value: None"


class TestForbiddenPatternsOnlyInCodeBlocks:
    """Test that forbidden patterns are only checked inside {{ }} and {% %} blocks."""

    def test_text_outside_braces_with_forbidden_pattern_substrings(self):
        """Text outside {{ }} containing pattern substrings like 'os.' should be allowed."""
        template = """
        You are a helpful assistant for Cross-functional teams.
        We support Windows, macOS, Linux, and other operating systems.

        User: {{ user_name }}
        Project: {{ project_name }}
        """

        context = {"user_name": "Alice", "project_name": "SystemOS"}

        # This should work fine - "Cross" contains "os.", "macOS" contains "os.", etc.
        result = render_system_prompt_template(template, context)

        assert "Cross-functional" in result
        assert "macOS" in result
        assert "Alice" in result
        assert "SystemOS" in result

    def test_malicious_code_inside_braces_is_blocked(self):
        """Malicious code inside {{ }} should still be blocked."""
        template = "{{ os.popen('ls').read() }}"

        with pytest.raises(TemplateSecurityError) as exc_info:
            render_system_prompt_template(template, {})

        error_message = str(exc_info.value)
        assert "os." in error_message.lower() or "forbidden" in error_message.lower()

    def test_text_with_subprocess_keyword_outside_braces(self):
        """Text containing 'subprocess' keyword outside {{ }} should be allowed."""
        template = """
        The subprocess management system handles background tasks.

        Status: {{ status }}
        """

        context = {"status": "running"}

        result = render_system_prompt_template(template, context)

        assert "subprocess management" in result
        assert "running" in result

    def test_malicious_subprocess_code_inside_braces_is_blocked(self):
        """Malicious subprocess code inside {{ }} should be blocked."""
        template = "{{ subprocess.check_output(['id']) }}"

        with pytest.raises(TemplateSecurityError) as exc_info:
            render_system_prompt_template(template, {})

        error_message = str(exc_info.value)
        assert "subprocess" in error_message.lower() or "forbidden" in error_message.lower()


class TestSecurityLogging:
    """Test that security violations are properly logged."""

    def test_security_violation_raises_with_message(self):
        """Security violations should raise TemplateSecurityError with descriptive message."""
        payload = "{{ ''.__class__ }}"
        context = {}

        with pytest.raises(TemplateSecurityError) as exc_info:
            render_secure_template(payload, context, context_name="log_test")

        # Check that the error message is descriptive
        error_message = str(exc_info.value)
        assert "security" in error_message.lower() or "forbidden" in error_message.lower()
        assert "__class__" in error_message or "injection" in error_message.lower()


class TestJSONBlockDetection:
    """Test detection of JSON content vs Jinja2 template syntax."""

    def test_is_json_content_detects_json_object(self):
        """JSON objects should be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        json_content = ' "template": { "name": "Test" } '
        assert _is_json_content(json_content) is True

    def test_is_json_content_detects_json_with_string_key(self):
        """JSON with quoted keys should be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        json_content = '"key": "value"'
        assert _is_json_content(json_content) is True

    def test_is_json_content_detects_json_with_numeric_value(self):
        """JSON with numeric values should be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        json_content = '"count": 123'
        assert _is_json_content(json_content) is True

    def test_is_json_content_detects_json_array(self):
        """JSON arrays should be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        json_content = '[ {"id": "CON1"} ]'
        assert _is_json_content(json_content) is True

    def test_is_json_content_rejects_jinja2_variable(self):
        """Simple Jinja2 variables should not be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        jinja_content = ' user_name '
        assert _is_json_content(jinja_content) is False

    def test_is_json_content_rejects_jinja2_property_access(self):
        """Jinja2 property access should not be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        jinja_content = ' user.name '
        assert _is_json_content(jinja_content) is False

    def test_is_json_content_rejects_jinja2_filter(self):
        """Jinja2 filters should not be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        jinja_content = ' items|length '
        assert _is_json_content(jinja_content) is False

    def test_is_json_content_rejects_empty_content(self):
        """Empty content should not be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        assert _is_json_content('') is False
        assert _is_json_content('   ') is False


class TestTemplateTypeDetection:
    """Test detection of template types (plain text vs Jinja2)."""

    def test_detect_plain_text_with_only_json(self):
        """Templates with only JSON blocks should be detected as plain text."""
        from codemie.core.template_security import _detect_template_type

        template = '''
        Example JSON:
        {{ "template": { "name": "Test" } }}
        {{ "columns": [ {"id": "CON1"} ] }}
        '''
        assert _detect_template_type(template) == "plain_text"

    def test_detect_jinja2_with_variable(self):
        """Templates with Jinja2 variables should be detected as jinja2."""
        from codemie.core.template_security import _detect_template_type

        template = 'Hello {{ user_name }}!'
        assert _detect_template_type(template) == "jinja2"

    def test_detect_jinja2_with_control_structure(self):
        """Templates with Jinja2 control structures should be detected as jinja2."""
        from codemie.core.template_security import _detect_template_type

        template = '{% if active %}Active{% else %}Inactive{% endif %}'
        assert _detect_template_type(template) == "jinja2"

    def test_detect_plain_text_with_raw_tags(self):
        """Templates with only {% raw %} tags should be detected as plain text."""
        from codemie.core.template_security import _detect_template_type

        template = '{% raw %}{{ "data": "value" }}{% endraw %}'
        assert _detect_template_type(template) == "plain_text"

    def test_detect_jinja2_with_for_loop(self):
        """Templates with for loops should be detected as jinja2."""
        from codemie.core.template_security import _detect_template_type

        template = '{% for item in items %}{{ item }}{% endfor %}'
        assert _detect_template_type(template) == "jinja2"

    def test_detect_plain_text_without_braces(self):
        """Plain text without braces should be detected as plain text."""
        from codemie.core.template_security import _detect_template_type

        template = 'This is just plain text without any template syntax.'
        assert _detect_template_type(template) == "plain_text"

    def test_detect_jinja2_mixed_with_json(self):
        """Templates with both Jinja2 variables and JSON should be detected as jinja2."""
        from codemie.core.template_security import _detect_template_type

        template = '''
        User: {{ user_name }}

        Example JSON:
        {{ "status": "active" }}
        '''
        assert _detect_template_type(template) == "jinja2"


class TestAutoWrapJSONBlocks:
    """Test automatic wrapping of JSON blocks in {% raw %} tags."""

    def test_auto_wrap_single_json_block(self):
        """Single JSON block should be wrapped in {% raw %} tags."""
        from codemie.core.template_security import _auto_wrap_json_blocks

        template = '{{ "key": "value" }}'
        result = _auto_wrap_json_blocks(template)
        assert '{% raw %}{{ "key": "value" }}{% endraw %}' in result

    def test_auto_wrap_multiple_json_blocks(self):
        """Multiple JSON blocks should each be wrapped."""
        from codemie.core.template_security import _auto_wrap_json_blocks

        template = '''
        {{ "template": { "name": "Test" } }}
        {{ "columns": [ {"id": "CON1"} ] }}
        '''
        result = _auto_wrap_json_blocks(template)
        assert result.count('{% raw %}') == 2
        assert result.count('{% endraw %}') == 2

    def test_auto_wrap_preserves_jinja2_variables(self):
        """Jinja2 variables should not be wrapped."""
        from codemie.core.template_security import _auto_wrap_json_blocks

        template = 'Hello {{ user_name }}!'
        result = _auto_wrap_json_blocks(template)
        assert result == 'Hello {{ user_name }}!'
        assert '{% raw %}' not in result

    def test_auto_wrap_mixed_content(self):
        """Mixed Jinja2 and JSON should wrap only JSON."""
        from codemie.core.template_security import _auto_wrap_json_blocks

        template = '''
        User: {{ user_name }}
        Config: {{ "setting": "value" }}
        '''
        result = _auto_wrap_json_blocks(template)
        # Jinja2 variable should remain unwrapped
        assert 'User: {{ user_name }}' in result
        # JSON should be wrapped
        assert '{% raw %}{{ "setting": "value" }}{% endraw %}' in result

    def test_auto_wrap_nested_json(self):
        """Nested JSON structures should be wrapped."""
        from codemie.core.template_security import _auto_wrap_json_blocks

        template = '{{ "outer": { "inner": { "value": 123 } } }}'
        result = _auto_wrap_json_blocks(template)
        assert '{% raw %}' in result
        assert '{% endraw %}' in result


class TestJSONBlocksIntegration:
    """Integration tests for JSON block handling in template rendering."""

    def test_render_json_only_template(self):
        """Templates with only JSON should be returned as-is."""
        template = '''
        Example JSON output:
        {{ "template": { "name": "Test" } }}
        {{ "columns": [ {"id": "CON1"} ] }}
        '''
        result = render_secure_template(template, {})
        # Should return template as-is since it's plain text
        assert '"template"' in result
        assert '"columns"' in result

    def test_render_mixed_jinja2_and_json(self):
        """Templates with both Jinja2 and JSON should render correctly."""
        template = '''
        User: {{ user_name }}

        Example output:
        {{ "status": "active" }}
        {{ "count": 42 }}
        '''
        result = render_secure_template(template, {"user_name": "Alice"})
        # Jinja2 variable should be rendered
        assert 'User: Alice' in result
        # JSON should be preserved
        assert '"status": "active"' in result
        assert '"count": 42' in result

    def test_render_assistant_prompt_with_json_examples(self):
        """Real-world assistant prompt with JSON examples should work."""
        prompt = '''
SYSTEM: You compile JSON.

Example JSON:
{{ "template": { "name": "Test" } }}
{{ "columns": [ {"id": "CON1"} ] }}
{{ "dataRows": [ {"desc": "Rule1"} ] }}

User context: {{ user_role }}
        '''
        result = render_secure_template(prompt, {"user_role": "Admin"})
        # JSON examples should be preserved
        assert '"template"' in result
        assert '"columns"' in result
        assert '"dataRows"' in result
        # Jinja2 variable should be rendered
        assert 'Admin' in result

    def test_render_json_with_colons_in_values(self):
        """JSON with colons in string values should work."""
        template = '{{ "url": "http://example.com:8080" }}'
        result = render_secure_template(template, {})
        assert 'http://example.com:8080' in result

    def test_render_complex_nested_json(self):
        """Complex nested JSON structures should be preserved."""
        template = '''
        {{ "config": {
            "server": {
                "host": "localhost",
                "port": 8080
            },
            "features": ["auth", "logging"]
        } }}
        '''
        result = render_secure_template(template, {})
        assert '"config"' in result
        assert '"server"' in result
        assert '"features"' in result

    def test_render_json_arrays(self):
        """JSON arrays should be preserved."""
        template = '{{ [ "item1", "item2", "item3" ] }}'
        result = render_secure_template(template, {})
        assert '"item1"' in result
        assert '"item2"' in result
        assert '"item3"' in result

    def test_security_checks_still_work_with_json(self):
        """Security checks should still work even with JSON detection."""
        # JSON with malicious Jinja2 mixed in should be caught
        malicious_template = '''
        {{ user_name }}
        {{ config.__class__ }}
        '''
        with pytest.raises(TemplateSecurityError):
            render_secure_template(malicious_template, {"user_name": "test"})


class TestEdgeCasesAndCornerCases:
    """Test edge cases and corner cases for JSON/Jinja2 detection."""

    def test_single_curly_brace_not_treated_as_template(self):
        """Single curly braces should not be treated as template syntax."""
        template = 'This { is } not a template'
        result = render_secure_template(template, {})
        assert result == template

    def test_escaped_curly_braces(self):
        """Escaped curly braces should be handled correctly."""
        template = 'To show {{ in text, escape it'
        result = render_secure_template(template, {})
        # Should return as-is since it's not valid Jinja2
        assert '{{' in result

    def test_json_with_boolean_values(self):
        """JSON with boolean values should be detected correctly."""
        from codemie.core.template_security import _is_json_content

        json_content = '"enabled": true'
        assert _is_json_content(json_content) is True

    def test_json_with_null_values(self):
        """JSON with null values should be detected correctly."""
        from codemie.core.template_security import _is_json_content

        json_content = '"value": null'
        assert _is_json_content(json_content) is True

    def test_whitespace_handling(self):
        """Whitespace in JSON and Jinja2 should be handled correctly."""
        template = '''
        {{    "key"   :   "value"    }}
        {{    user_name    }}
        '''
        result = render_secure_template(template, {"user_name": "test"})
        assert '"key"' in result or 'test' in result

    def test_multiline_json_object(self):
        """Multiline JSON objects should be detected as JSON."""
        from codemie.core.template_security import _is_json_content

        json_content = '''
        "config": {
            "nested": "value"
        }
        '''
        assert _is_json_content(json_content) is True

    def test_json_with_special_characters(self):
        """JSON with special characters should be preserved."""
        template = '{{ "message": "Hello\\nWorld!" }}'
        result = render_secure_template(template, {})
        assert '"message"' in result

    def test_empty_template(self):
        """Empty template should be handled gracefully."""
        template = ''
        result = render_secure_template(template, {})
        assert result == ''

    def test_template_with_only_whitespace(self):
        """Template with only whitespace should be handled gracefully."""
        template = '   \n\n   '
        result = render_secure_template(template, {})
        assert result == template


class TestAutoescapeParameter:
    """Tests for the autoescape parameter added to render_secure_template (EPMCDME-10987).

    autoescape=True (default) is appropriate for HTML/system-prompt contexts where
    XSS prevention matters. autoescape=False is required for non-HTML workflow data
    (TransformNode, TemplateRenderer) to avoid mangling values that contain '&', '<', '>'.
    """

    def test_autoescape_true_by_default_escapes_ampersand(self):
        """Default autoescape=True must HTML-escape '&' to prevent XSS in HTML contexts."""
        result = render_secure_template("{{ value }}", {"value": "A & B"})

        assert result == "A &amp; B"

    def test_autoescape_true_by_default_escapes_angle_brackets(self):
        """Default autoescape=True must HTML-escape '<' and '>'."""
        result = render_secure_template("{{ value }}", {"value": "<b>bold</b>"})

        assert result == "&lt;b&gt;bold&lt;/b&gt;"

    def test_autoescape_false_preserves_ampersand(self):
        """autoescape=False must not escape '&' — correct for non-HTML workflow output."""
        result = render_secure_template("{{ value }}", {"value": "A & B"}, autoescape=False)

        assert result == "A & B"

    def test_autoescape_false_preserves_angle_brackets(self):
        """autoescape=False must not escape '<' and '>' in output."""
        result = render_secure_template("{{ value }}", {"value": "score <10 or >90"}, autoescape=False)

        assert result == "score <10 or >90"

    def test_autoescape_false_still_blocks_forbidden_patterns(self):
        """SSTI forbidden patterns must be blocked regardless of the autoescape setting."""
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"

        with pytest.raises(TemplateSecurityError):
            render_secure_template(payload, {}, autoescape=False)

    def test_autoescape_false_still_blocks_private_attribute_access_via_sandbox(self):
        """Sandbox must block private attribute access even when autoescape is disabled.

        validate_input=False bypasses the pattern check so only the RestrictedSandboxEnvironment
        is responsible for blocking. Accessing __class__ on a string (a '_'-prefixed attribute)
        must be rejected by is_safe_attribute regardless of the autoescape setting.
        """
        payload = "{{ ''.__class__ }}"

        with pytest.raises(TemplateSecurityError):
            render_secure_template(payload, {}, autoescape=False, validate_input=False)
