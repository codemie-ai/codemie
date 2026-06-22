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

"""
AST-based security checker for code executor.

Replaces regex-based pattern matching with AST analysis to detect bypass techniques
that evade static text analysis (e.g., __import__, string concatenation, chr() construction).

Pattern based on codemie/workflows/utils/safe_eval.py
"""

import ast
import logging
from dataclasses import dataclass
from typing import List

from llm_sandbox.security import SecurityIssueSeverity, SecurityPattern

logger = logging.getLogger(__name__)


@dataclass
class SecurityViolation:
    """Security violation detected by AST analysis."""

    description: str
    severity: SecurityIssueSeverity
    line_number: int
    node_type: str


class CodeExecutorSecurityChecker(ast.NodeVisitor):
    def __init__(self, severity_threshold: SecurityIssueSeverity):
        self.violations: List[SecurityViolation] = []
        self.threshold = severity_threshold

    def visit_Call(self, node: ast.Call) -> None:
        """Check function calls for dangerous patterns."""

        # Detect: __import__(...)
        if isinstance(node.func, ast.Name):
            if node.func.id == "__import__":
                self._add_violation(
                    node,
                    "Dynamic import not allowed",
                    SecurityIssueSeverity.HIGH,
                )

            # Detect: exec(...), eval(...), compile(...)
            elif node.func.id in ["exec", "eval", "compile"]:
                self._add_violation(
                    node,
                    f"{node.func.id}() not allowed",
                    SecurityIssueSeverity.HIGH,
                )

            # Detect: getattr(...)
            elif node.func.id == "getattr":
                self._check_getattr_call(node)

        # Continue visiting child nodes
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check attribute access for __builtins__ access."""

        # Detect: __builtins__.__import__ or __builtins__.anything
        if isinstance(node.value, ast.Name) and node.value.id == "__builtins__":
            self._add_violation(
                node,
                f"Access to __builtins__.{node.attr} not allowed",
                SecurityIssueSeverity.HIGH,
            )

        self.generic_visit(node)

    def _check_getattr_call(self, node: ast.Call) -> None:
        """Check getattr() calls for dangerous patterns."""

        if len(node.args) < 2:
            return

        first_arg = node.args[0]
        second_arg = node.args[1]

        # Detect: getattr(__builtins__, ...)
        if isinstance(first_arg, ast.Name) and first_arg.id == "__builtins__":
            attr_name = self._extract_string_value(second_arg)
            self._add_violation(
                node,
                f"getattr(__builtins__, '{attr_name}') not allowed",
                SecurityIssueSeverity.HIGH,
            )

    def _extract_string_value(self, node: ast.AST) -> str:
        """Extract string value from a node if possible."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return "<dynamic>"

    def _add_violation(self, node: ast.AST, description: str, severity: SecurityIssueSeverity) -> None:
        """Add a security violation."""

        # Only add violations at or above threshold
        if severity >= self.threshold:
            violation = SecurityViolation(
                description=description,
                severity=severity,
                line_number=getattr(node, "lineno", 0),
                node_type=type(node).__name__,
            )
            self.violations.append(violation)
            logger.debug(f"AST violation detected: {description} at line {violation.line_number}")


def check_code_with_ast(code: str, severity_threshold: SecurityIssueSeverity) -> tuple[bool, List[SecurityPattern]]:
    """
    Check Python code using AST analysis for security violations.

    Args:
        code: Python code to analyze
        severity_threshold: Minimum severity level to enforce

    Returns:
        tuple: (is_safe, violations) where is_safe is False if violations were found
    """

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.warning(f"Syntax error in code: {e}")
        violation = SecurityPattern(
            pattern="<syntax-error>",
            description=f"Invalid Python syntax: {str(e)}",
            severity=SecurityIssueSeverity.HIGH,
        )
        return False, [violation]

    checker = CodeExecutorSecurityChecker(severity_threshold)
    checker.visit(tree)

    patterns = [
        SecurityPattern(
            pattern=v.node_type,
            description=v.description,
            severity=v.severity,
        )
        for v in checker.violations
    ]

    is_safe = len(patterns) == 0

    if not is_safe:
        logger.info(f"AST security check failed: {len(patterns)} violation(s) detected")

    return is_safe, patterns
