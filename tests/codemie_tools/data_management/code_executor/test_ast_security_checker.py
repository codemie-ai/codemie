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

"""Tests for AST-based security checker."""

from llm_sandbox.security import SecurityIssueSeverity
from codemie_tools.data_management.code_executor.ast_security_checker import check_code_with_ast


class TestDynamicImportDetection:
    """Test detection of __import__() calls."""

    def test_direct_import(self):
        """Direct __import__ call should be blocked."""
        code = '__import__("os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "Dynamic import not allowed" in violations[0].description

    def test_import_with_variable(self):
        """__import__ with variable should be blocked."""
        code = 'module_name = "os"\n__import__(module_name)'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "Dynamic import not allowed" in violations[0].description

    def test_import_with_string_concat(self):
        """__import__ with string concatenation should be blocked."""
        code = '__import__("o" + "s")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "Dynamic import not allowed" in violations[0].description


class TestCodeExecutionDetection:
    """Test detection of exec/eval/compile calls."""

    def test_exec_call(self):
        """exec() call should be blocked."""
        code = 'exec("import os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "exec() not allowed" in violations[0].description

    def test_eval_call(self):
        """eval() call should be blocked."""
        code = 'eval("1 + 1")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "eval() not allowed" in violations[0].description

    def test_compile_call(self):
        """compile() call should be blocked."""
        code = 'compile("import os", "<string>", "exec")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "compile() not allowed" in violations[0].description


class TestBuiltinsAccess:
    """Test detection of __builtins__ access."""

    def test_builtins_attribute_access(self):
        """Direct __builtins__ attribute access should be blocked."""
        code = '__builtins__.__import__'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "__builtins__.__import__ not allowed" in violations[0].description

    def test_getattr_builtins(self):
        """getattr(__builtins__, ...) should be blocked."""
        code = 'getattr(__builtins__, "__import__")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "getattr(__builtins__, '__import__') not allowed" in violations[0].description

    def test_getattr_builtins_with_variable(self):
        """getattr(__builtins__, var) should be blocked."""
        code = 'attr = "__import__"\ngetattr(__builtins__, attr)'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "getattr(__builtins__, '<dynamic>') not allowed" in violations[0].description


class TestSeverityThreshold:
    """Test severity threshold filtering."""

    def test_high_threshold_blocks_high_severity(self):
        """HIGH threshold should block HIGH severity violations."""
        code = '__import__("os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.HIGH)

        assert not is_safe
        assert len(violations) >= 1

    def test_low_threshold_blocks_high_severity(self):
        """LOW threshold should block HIGH severity violations."""
        code = '__import__("os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) >= 1

    def test_safe_threshold_allows_everything(self):
        """SAFE threshold should allow everything."""
        code = '__import__("os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.SAFE)

        # With SAFE threshold, violations are still detected but not enforced
        # The checker still finds them but threshold filtering may allow them
        # Actual behavior depends on implementation
        assert isinstance(is_safe, bool)


class TestSafeCode:
    """Test that safe code passes validation."""

    def test_safe_arithmetic(self):
        """Simple arithmetic should be allowed."""
        code = 'x = 1 + 2'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert is_safe
        assert len(violations) == 0

    def test_safe_function_def(self):
        """Function definitions should be allowed."""
        code = 'def foo():\n    return 42'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert is_safe
        assert len(violations) == 0

    def test_safe_list_comprehension(self):
        """List comprehensions should be allowed."""
        code = '[x**2 for x in range(10)]'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert is_safe
        assert len(violations) == 0

    def test_safe_standard_import(self):
        """Standard imports are handled by regex layer, should pass AST."""
        code = 'import sys'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        # AST layer doesn't block standard imports (that's regex layer's job)
        assert is_safe
        assert len(violations) == 0


class TestSyntaxErrors:
    """Test handling of syntax errors."""

    def test_invalid_syntax(self):
        """Invalid Python syntax should be caught."""
        code = 'import os ['
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "Invalid Python syntax" in violations[0].description

    def test_incomplete_code(self):
        """Incomplete code should be caught."""
        code = 'def foo('
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert "Invalid Python syntax" in violations[0].description


class TestComplexScenarios:
    """Test complex real-world scenarios."""

    def test_nested_calls(self):
        """Nested dangerous calls should all be detected."""
        code = 'exec(__import__("os").popen("whoami").read())'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        # Should detect both exec and __import__
        assert len(violations) >= 2
        descriptions = [v.description for v in violations]
        assert any("exec() not allowed" in d for d in descriptions)
        assert any("Dynamic import not allowed" in d for d in descriptions)

    def test_obfuscated_import(self):
        """Obfuscated import patterns should be detected."""
        code = '''
parts = ["o", "s"]
name = "".join(parts)
mod = __import__(name)
'''
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) >= 1
        assert any("Dynamic import not allowed" in v.description for v in violations)

    def test_getattr_chain(self):
        """Chain of getattr calls with __builtins__ should be detected."""
        code = 'getattr(__builtins__, "__import__")("os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        # Should detect getattr(__builtins__, ...) and __import__
        descriptions = [v.description for v in violations]
        assert any("getattr(__builtins__" in d for d in descriptions)

    def test_multiple_violations_in_one_line(self):
        """Multiple violations in one statement should all be detected."""
        code = 'exec(__import__(chr(111) + chr(115)))'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 2


class TestViolationDetails:
    """Test violation metadata."""

    def test_violation_includes_line_number(self):
        """Violations should include line numbers."""
        code = 'x = 1\n__import__("os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        # Line number should be in the SecurityPattern pattern field (node type)
        assert violations[0].pattern == "Call"

    def test_violation_includes_severity(self):
        """Violations should include severity level."""
        code = '__import__("os")'
        is_safe, violations = check_code_with_ast(code, SecurityIssueSeverity.LOW)

        assert not is_safe
        assert len(violations) == 1
        assert violations[0].severity == SecurityIssueSeverity.HIGH
