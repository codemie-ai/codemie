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
Secure Jinja2 template rendering utilities to prevent Server-Side Template Injection (SSTI) attacks.

This module provides secure template rendering with the following protections:
1. Sandboxed execution environment
2. Restricted attribute access
3. Input validation against known attack patterns
4. Whitelisted template variables
5. Comprehensive logging and monitoring

Security Reference: https://onsecurity.io/article/server-side-template-injection-with-jinja2/
"""

import re
from typing import Any, Optional

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment, SecurityError

from codemie.configs.logger import logger


# Forbidden patterns that indicate SSTI attack attempts
FORBIDDEN_PATTERNS = [
    r'__class__',
    r'__mro__',
    r'__subclasses__',
    r'__bases__',
    r'__globals__',
    r'__init__',
    r'__import__',
    r'__builtins__',
    r'__code__',
    r'__closure__',
    r'__func__',
    r'__self__',
    r'func_globals',
    r'func_code',
    r'\beval\s*\(',
    r'\bexec\s*\(',
    r'\bcompile\s*\(',
    r'\b__import__\s*\(',
    r'os\.',
    r'subprocess',
    r'sys\.',
    r'open\s*\(',
    r'file\s*\(',
    r'input\s*\(',
    r'raw_input\s*\(',
]

# Compile patterns once for performance
COMPILED_FORBIDDEN_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in FORBIDDEN_PATTERNS]


class TemplateSecurityError(SecurityError):
    """Raised when a template security violation is detected."""

    pass


class RestrictedSandboxEnvironment(SandboxedEnvironment):
    """
    Custom sandboxed Jinja2 environment with additional security restrictions.

    This environment:
    - Blocks access to dangerous Python attributes
    - Enables HTML autoescaping by default
    - Provides detailed security logging
    - Restricts unsafe method calls
    """

    def __init__(self, *args, **kwargs):
        """Initialize the restricted sandbox environment with secure defaults."""
        # Set secure defaults
        kwargs.setdefault('autoescape', True)
        kwargs.setdefault('trim_blocks', True)
        kwargs.setdefault('lstrip_blocks', True)

        super().__init__(*args, **kwargs)

        # Additional security: disable potentially dangerous filters and tests
        self.filters.pop('attr', None)  # Remove attr filter which can access attributes
        self.tests.pop('callable', None)  # Remove callable test

        logger.debug("Initialized RestrictedSandboxEnvironment with security restrictions")

    def is_safe_attribute(self, obj, attr, value):
        """
        Override to block access to dangerous attributes.

        Args:
            obj: The object being accessed
            attr: The attribute name being accessed
            value: The attribute value

        Returns:
            bool: True if access is allowed, raises SecurityError otherwise

        Raises:
            TemplateSecurityError: If access to dangerous attribute is attempted
        """
        # Block access to private attributes (those starting with underscore)
        if attr.startswith('_'):
            logger.warning(
                f"Template security violation: Attempt to access private attribute '{attr}'. "
                f"Attribute={attr}, ObjectType={type(obj).__name__}"
            )
            raise TemplateSecurityError(f"Access to private attribute '{attr}' is forbidden for security reasons")

        # Forbidden attributes that could be used for code execution
        forbidden_attrs = {
            '__class__',
            '__mro__',
            '__subclasses__',
            '__bases__',
            '__globals__',
            '__init__',
            '__import__',
            '__builtins__',
            '__code__',
            '__closure__',
            '__func__',
            '__self__',
            'func_globals',
            'func_code',
            'gi_frame',
            'gi_code',
            'cr_frame',
            'cr_code',
        }

        if attr in forbidden_attrs:
            logger.error(
                f"Template security violation: Attempt to access forbidden attribute '{attr}'. "
                f"Attribute={attr}, ObjectType={type(obj).__name__}"
            )
            raise TemplateSecurityError(f"Access to attribute '{attr}' is forbidden for security reasons")

        # Call parent implementation for additional checks
        return super().is_safe_attribute(obj, attr, value)

    def call_safe(self, __context, __obj, *args, **kwargs):
        """
        Override to add logging for safe calls.

        This helps with security monitoring and debugging.
        """
        try:
            return super().call(__context, __obj, *args, **kwargs)
        except SecurityError as e:
            logger.warning(
                f"Template security violation in call: {str(e)}. "
                f"Callable={str(__obj)}, Args={str(args)}, Kwargs={str(kwargs)}"
            )
            raise


def _is_json_content(content: str) -> bool:
    """
    Check if content inside {{ }} looks like JSON rather than Jinja2.

    JSON inside {{ }} typically starts with a quote or brace and contains colons in key-value pairs.
    Jinja2 variables are identifiers, property access, or expressions.

    Args:
        content: The content between {{ and }} to check

    Returns:
        bool: True if content looks like JSON, False if it looks like Jinja2
    """
    stripped = content.strip()

    # Empty content is not JSON
    if not stripped:
        return False

    # If it starts with a quote and contains a colon followed by any value, it's likely JSON
    # Examples: "key": "value", "key": 123, "key": {...}
    # Check if there's a colon that's not part of a ternary operator
    # JSON pattern: "something": value
    # Jinja2 pattern: condition if x else y (no quotes before colon)
    if stripped.startswith(('"', "'")) and re.search(r'^["\'][^"\']+["\']\s*:\s*.+', stripped):
        return True

    # If it starts with { or [ and looks like a JSON structure, it's JSON
    # Examples: { "key": "value" }, [ "item1", "item2" ]
    if stripped.startswith('{'):
        # Objects must have quoted keys with colons
        return bool(re.search(r'["\'][^"\']+["\']\s*:', stripped))
    elif stripped.startswith('['):
        # Arrays with quoted strings are JSON
        return bool(re.search(r'["\']', stripped))

    return False


def _extract_jinja_code_blocks(template_str: str) -> list[str]:
    """
    Extract only the code blocks from Jinja2 template (content inside {{ }} and {% %}).
    Filters out JSON content that may be inside {{ }} in plain text examples.

    Args:
        template_str: The template string to extract from

    Returns:
        list[str]: List of code blocks extracted from the template
    """
    code_blocks = []

    # Extract {{ variable }} blocks
    variable_pattern = re.compile(r'\{\{(.+?)}}', re.DOTALL)
    variable_blocks = variable_pattern.findall(template_str)

    # Filter out JSON content - only keep actual Jinja2 expressions
    jinja_variable_blocks = [block for block in variable_blocks if not _is_json_content(block)]
    code_blocks.extend(jinja_variable_blocks)

    logger.debug(
        f"Extracted {len(variable_blocks)} variable blocks, "
        f"filtered to {len(jinja_variable_blocks)} Jinja2 blocks "
        f"(excluded {len(variable_blocks) - len(jinja_variable_blocks)} JSON blocks)"
    )

    # Extract {% statement %} blocks
    statement_pattern = re.compile(r'\{%(.+?)%}', re.DOTALL)
    statement_blocks = statement_pattern.findall(template_str)
    code_blocks.extend(statement_blocks)

    logger.debug(f"Extracted {len(statement_blocks)} statement blocks: {statement_blocks}")
    logger.debug(f"Total code blocks extracted: {len(code_blocks)}")

    return code_blocks


def _check_forbidden_patterns(template_str: str, context_name: str) -> None:
    """
    Check template string for patterns that indicate SSTI attack attempts.
    Only checks code inside {{ }} and {% %} blocks, not plain text.

    Args:
        template_str: The template string to check
        context_name: Name of the context (for logging purposes)

    Raises:
        TemplateSecurityError: If a forbidden pattern is found
    """
    # Extract only Jinja2 code blocks (content inside {{ }} and {% %})
    code_blocks = _extract_jinja_code_blocks(template_str)

    logger.debug(
        f"Checking {len(code_blocks)} code blocks for forbidden patterns in {context_name}. "
        f"TemplatePreview={template_str[:200]}"
    )

    # Check forbidden patterns only in code blocks, not in plain text
    for i, code_block in enumerate(code_blocks):
        logger.debug(f"Checking code block {i + 1}/{len(code_blocks)}: {code_block[:100]}")

        for pattern in COMPILED_FORBIDDEN_PATTERNS:
            if pattern.search(code_block):
                logger.error(
                    f"Template security violation: Forbidden pattern detected in {context_name}. "
                    f"Pattern={pattern.pattern}, Context={context_name}, CodeBlock={code_block[:100]}"
                )
                raise TemplateSecurityError(
                    f"Template contains forbidden pattern that could be used for code injection. "
                    f"Pattern: {pattern.pattern}. Please review your template for security issues."
                )

        logger.debug(f"Code block {i + 1} passed all pattern checks")

    logger.debug(f"All {len(code_blocks)} code blocks passed security validation for {context_name}")


def _has_legitimate_spaces(expr: str) -> bool:
    """
    Check if spaces in expression are legitimate (e.g., in operators or function calls).

    Args:
        expr: The expression to check

    Returns:
        bool: True if spaces are legitimate, False otherwise
    """
    operators = [' in ', ' not ', ' and ', ' or ', ' is ', '==', '!=', '>=', '<=', '(', ')']
    return any(op in expr for op in operators)


def _extract_base_variable(expr: str) -> str:
    """
    Extract the base variable name from an expression.

    Args:
        expr: The expression to extract from

    Returns:
        str: The base variable name
    """
    tokens = re.split(r'[\s.\[\]()|]+', expr)  # No need to escape characters in character class
    return tokens[0] if tokens else expr


def _validate_variable_expression(var_expr: str, template_str: str, context_name: str) -> None:
    """
    Validate a single variable expression from Jinja2 templates.

    Args:
        var_expr: The variable expression to validate
        template_str: Original template string (for error reporting)
        context_name: Name of the context (for logging purposes)

    Raises:
        TemplateSecurityError: If an invalid variable expression is found
    """
    stripped_expr = var_expr.strip()

    # Check for invalid multi-word identifiers
    if ' ' in stripped_expr and not _has_legitimate_spaces(stripped_expr):
        logger.error(
            f"Template security violation: Invalid variable expression '{stripped_expr}' in {context_name}. "
            f"Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123'). "
            f"Variable={stripped_expr}, Context={context_name}, TemplatePreview={template_str[:100]}"
        )
        raise TemplateSecurityError(
            f"Template contains invalid variable expression '{stripped_expr}'. "
            f"Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123'). "
            f"Spaces are not allowed in variable names."
        )

    # Extract the base identifier
    base_var = _extract_base_variable(stripped_expr)

    # Skip if it's not a variable (e.g., strings, numbers)
    if not base_var or base_var[0].isdigit() or base_var.startswith(("'", '"')):
        return

    # Validate the base variable name
    if not validate_variable_key(base_var):
        logger.error(
            f"Template security violation: Invalid variable name '{base_var}' in {context_name}. "
            f"Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123'). "
            f"Variable={base_var}, Context={context_name}, TemplatePreview={template_str[:100]}"
        )
        raise TemplateSecurityError(
            f"Template contains invalid variable name '{base_var}'. "
            f"Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123'). "
            f"Spaces and special characters are not allowed in variable names."
        )


def _detect_template_type(template_str: str) -> str:
    """
    Detect if a string contains actual Jinja2 template syntax or is just plain text with JSON examples.

    Args:
        template_str: The string to analyze

    Returns:
        str: "jinja2" if it contains Jinja2 syntax, "plain_text" if it's just text (possibly with JSON examples)
    """
    # Extract all {{ }} blocks
    variable_pattern = re.compile(r'\{\{(.+?)}}', re.DOTALL)
    variable_blocks = variable_pattern.findall(template_str)

    # Extract all {% %} blocks (but exclude {% raw %} and {% endraw %} as they're meta-directives)
    statement_pattern = re.compile(r'\{%(.+?)%}', re.DOTALL)
    statement_blocks = statement_pattern.findall(template_str)

    # Filter out {% raw %} and {% endraw %} tags as they're used to escape JSON
    jinja2_statements = [
        stmt for stmt in statement_blocks if stmt.strip().lower() not in ('raw', 'endraw', '- raw', '- endraw')
    ]

    # If there are real Jinja2 statements (not just raw tags), it's definitely Jinja2
    if jinja2_statements:
        return "jinja2"

    # Check if any {{ }} blocks look like Jinja2 (not JSON)
    has_jinja2_syntax = False
    for block in variable_blocks:
        if not _is_json_content(block):
            # This looks like a Jinja2 expression, not JSON
            has_jinja2_syntax = True
            break

    return "jinja2" if has_jinja2_syntax else "plain_text"


def _auto_wrap_json_blocks(template_str: str) -> str:
    """
    Automatically wrap JSON-looking blocks in {% raw %} tags to prevent Jinja2 parsing errors.

    This function identifies {{ }} blocks that contain JSON syntax and wraps them in {% raw %}...{% endraw %}
    so Jinja2 treats them as plain text instead of attempting to parse them as Jinja2 expressions.

    Args:
        template_str: The template string potentially containing JSON blocks

    Returns:
        str: Template string with JSON blocks wrapped in {% raw %} tags
    """
    # Pattern to match {{ }} blocks
    variable_pattern = re.compile(r'\{\{(.+?)\}\}', re.DOTALL)

    def replace_json_block(match):
        """Replace JSON blocks with raw-wrapped versions."""
        full_match = match.group(0)  # Full {{ ... }} including braces
        content = match.group(1)  # Content inside braces

        if _is_json_content(content):
            # This is JSON, wrap it in raw tags
            return f'{{% raw %}}{full_match}{{% endraw %}}'
        else:
            # This is Jinja2, leave it as-is
            return full_match

    # Replace all {{ }} blocks, wrapping JSON ones
    result = variable_pattern.sub(replace_json_block, template_str)

    logger.debug(f"Auto-wrapped JSON blocks. Original length: {len(template_str)}, New length: {len(result)}")

    return result


def validate_template_string(template_str: str, context_name: str = "template") -> None:
    """
    Validate a template string for potential SSTI attack patterns and invalid variable names.
    Only validates if the string actually contains Jinja2 template syntax.

    Args:
        template_str: The template string to validate
        context_name: Name of the context (for logging purposes)

    Raises:
        TemplateSecurityError: If dangerous patterns or invalid variable names are detected
    """
    if not isinstance(template_str, str):
        return  # Non-string templates are safe

    logger.debug(
        f"Starting template validation for {context_name}. "
        f"TemplateLength={len(template_str)}, TemplatePreview={template_str[:200]}"
    )

    # Step 0: Detect if this is actually a Jinja2 template or just plain text with JSON
    template_type = _detect_template_type(template_str)
    logger.debug(f"Detected template type: {template_type} for {context_name}")

    if template_type == "plain_text":
        logger.debug(f"Template is plain text (no Jinja2 syntax detected), skipping validation for {context_name}")
        return

    # Step 1: Check for forbidden patterns that indicate SSTI attack attempts
    _check_forbidden_patterns(template_str, context_name)

    # Step 2: Extract variable expressions and control variables
    variable_pattern = re.compile(r'\{\{\s*([^}]+?)\s*}}')  # No need to escape } in the second part #NOSONAR
    control_pattern = re.compile(r'\{%\s*(?:for|if|set|with)\s+([a-zA-Z_][a-zA-Z0-9_\s]*?)\s+')

    all_variable_expressions = variable_pattern.findall(template_str)
    all_control_vars = control_pattern.findall(template_str)

    # Filter out JSON expressions
    jinja_variable_expressions = [expr for expr in all_variable_expressions if not _is_json_content(expr)]

    logger.debug(
        f"Found {len(all_variable_expressions)} total expressions, "
        f"{len(jinja_variable_expressions)} Jinja2 expressions "
        f"(filtered {len(all_variable_expressions) - len(jinja_variable_expressions)} JSON blocks), "
        f"and {len(all_control_vars)} control variables"
    )

    # Step 3: Validate each variable expression from {{ }} (only Jinja2, not JSON)
    for var_expr in jinja_variable_expressions:
        logger.debug(f"Validating variable expression: {var_expr}")
        _validate_variable_expression(var_expr, template_str, context_name)

    # Step 4: Validate control structure variables
    for var_name in all_control_vars:
        var_name = var_name.strip()
        logger.debug(f"Validating control variable: {var_name}")
        if not validate_variable_key(var_name):
            logger.error(
                f"Template security violation: Invalid variable name '{var_name}' in {context_name}. "
                f"Variable={var_name}, Context={context_name}, TemplatePreview={template_str[:100]}"
            )
            raise TemplateSecurityError(
                f"Template contains invalid variable name '{var_name}'. "
                f"Variable names must be alphanumeric with underscores only."
            )

    logger.debug(f"Template validation passed for {context_name}")


def validate_variable_key(key: str) -> bool:
    """
    Validate that a variable key name is safe for template rendering.

    Keys must be alphanumeric with underscores only (e.g., "project_name", "user_123").
    NO spaces or special characters allowed in key names.

    Args:
        key: The key name to validate

    Returns:
        bool: True if the key is safe, False otherwise
    """
    # Key must be alphanumeric with underscores only - NO spaces or special characters
    safe_key_pattern = re.compile(r'^\w+$')
    if not safe_key_pattern.match(key):
        logger.warning(
            f"Invalid characters in variable key: '{key}'. "
            f"Only alphanumeric characters and underscores allowed (e.g., 'project_name', 'user_123')."
        )
        return False
    return True


def sanitize_template_context(context: dict[str, Any], allowed_keys: Optional[set[str]] = None) -> dict[str, Any]:
    """
    Sanitize template context to only include safe variables.

    Args:
        context: The original template context
        allowed_keys: Optional set of allowed keys. If None, basic sanitization is performed.

    Returns:
        dict: Sanitized context with only allowed variables
    """
    import types

    sanitized = {}

    for key, value in context.items():
        # Validate key name (alphanumeric + underscore only)
        if not validate_variable_key(key):
            logger.warning(f"Skipping variable with invalid key name: '{key}'")
            continue

        # Skip private/dunder attributes
        if key.startswith('_'):
            logger.warning(f"Skipping private key '{key}' in template context")
            continue

        # If allowlist is provided, only include allowed keys
        if allowed_keys and key not in allowed_keys:
            logger.debug(f"Skipping non-whitelisted key '{key}' in template context")
            continue

        # Don't allow module, class, or function objects
        if isinstance(value, (type, types.ModuleType, types.FunctionType, types.MethodType)):
            logger.warning(f"Skipping potentially dangerous object for key '{key}': {type(value)}")
            continue

        # Don't allow objects with __module__ that aren't safe primitives
        if hasattr(value, '__module__') and not isinstance(value, (str, int, float, bool, list, dict, type(None))):
            logger.warning(f"Skipping potentially dangerous object for key '{key}': {type(value)}")
            continue

        sanitized[key] = value

    return sanitized


def render_secure_template(
    template_str: str,
    context: dict[str, Any],
    context_name: str = "template",
    allowed_context_keys: Optional[set[str]] = None,
    validate_input: bool = True,
) -> str:
    """
    Render a Jinja2 template with comprehensive security protections.

    This function:
    1. Detects if the string contains Jinja2 syntax or is plain text
    2. For plain text (no Jinja2), returns as-is without processing
    3. For Jinja2 templates:
       a. Validates the template string for attack patterns (optional)
       b. Sanitizes the context variables (validates keys are alphanumeric+underscore)
       c. Renders the template in a sandboxed environment
    4. Provides comprehensive error handling and logging

    Args:
        template_str: The Jinja2 template string to render
        context: Dictionary of variables to use in template rendering
        context_name: Name of the context (for logging)
        allowed_context_keys: Optional set of allowed context keys (whitelist)
        validate_input: Whether to validate template for attack patterns (default: True)

    Returns:
        str: The rendered template string

    Raises:
        TemplateSecurityError: If security violation is detected
        TemplateSyntaxError: If template has invalid syntax

    Example:
        >>> render_secure_template(
        ...     "Hello {{ name }}!",
        ...     {"name": "World"},
        ...     context_name="greeting"
        ... )
        'Hello World!'
    """
    try:
        # Step 0: Check if this is actually a Jinja2 template or just plain text
        template_type = _detect_template_type(template_str)
        logger.debug(f"Template type detected: {template_type} for {context_name}")

        if template_type == "plain_text":
            logger.debug(
                f"Template is plain text (no Jinja2 syntax), returning as-is for {context_name}. "
                f"ContextName={context_name}, OutputLength={len(template_str)}"
            )
            return template_str

        # Step 0.5: Auto-wrap JSON blocks in {% raw %} tags to prevent parsing errors
        processed_template = _auto_wrap_json_blocks(template_str)

        # Step 1: Validate template string for attack patterns
        if validate_input:
            validate_template_string(processed_template, context_name)

        # Step 2: Sanitize context (validates keys are alphanumeric+underscore)
        safe_context = sanitize_template_context(context, allowed_context_keys)

        # Step 3: Create secure sandbox environment
        env = RestrictedSandboxEnvironment()

        # Step 4: Render template
        template = env.from_string(processed_template)
        rendered = template.render(safe_context)

        logger.debug(
            f"Successfully rendered secure template for {context_name}. "
            f"ContextName={context_name}, ContextKeys={list(safe_context.keys())}, OutputLength={len(rendered)}"
        )

        return rendered

    except TemplateSyntaxError as e:
        logger.error(
            f"Template syntax error in {context_name}: {str(e)}. "
            f"ContextName={context_name}, Error={str(e)}, LineNumber={e.lineno}"
        )
        raise

    except SecurityError as e:
        logger.error(f"Template security error in {context_name}: {str(e)}. ContextName={context_name}, Error={str(e)}")
        raise TemplateSecurityError(f"Security violation detected while rendering {context_name}: {str(e)}")

    except Exception as e:
        logger.error(
            f"Unexpected error rendering template for {context_name}: {str(e)}. "
            f"ContextName={context_name}, Error={str(e)}",
            exc_info=True,
        )
        raise


# Convenience function with common allowed keys for system prompts
SYSTEM_PROMPT_ALLOWED_KEYS = {
    'current_user',
    'date',
    'username',
    'user_id',
    'project_name',
    'assistant_name',
    # Add other safe variables as needed
}


def render_system_prompt_template(
    template_str: str, context: dict[str, Any], allow_custom_variables: bool = True
) -> str:
    """
    Render a system prompt template with security protections.

    This is a convenience wrapper around render_secure_template specifically
    for rendering system prompts with appropriate security settings.
    Variable keys are validated to be alphanumeric with underscores only.

    Args:
        template_str: The system prompt template string
        context: Template variables (keys must be alphanumeric+underscore)
        allow_custom_variables: If False, only SYSTEM_PROMPT_ALLOWED_KEYS are allowed

    Returns:
        str: The rendered system prompt

    Raises:
        TemplateSecurityError: If security violation is detected
    """
    allowed_keys = None if allow_custom_variables else SYSTEM_PROMPT_ALLOWED_KEYS

    return render_secure_template(
        template_str=template_str,
        context=context,
        context_name="system_prompt",
        allowed_context_keys=allowed_keys,
        validate_input=True,
    )
