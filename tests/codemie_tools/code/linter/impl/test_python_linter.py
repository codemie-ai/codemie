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

import subprocess
from unittest.mock import patch

import pytest

from codemie_tools.code.linter.impl.python import PythonLinter


class TestPythonLinter:
    @pytest.fixture
    def linter(self):
        return PythonLinter(error_codes="E999,F821")

    old_code_valid = """
def foo():
    return 42
"""

    new_code_valid = """
def foo():
    return 42

def bar():
    return 42
"""

    new_code_indent_issue = """
def foo():
    return 42

def bar():
return 42
"""

    new_code_wrong_var_issue = """
def foo():
    return 42

def bar():
    return some
"""

    def test_lint_code_no_issues(self, linter):
        success, errors = linter.lint_code_diff(self.old_code_valid, self.new_code_valid)
        assert success
        assert not errors

    def test_lint_code_indent_issue(self, linter):
        success, errors = linter.lint_code_diff(self.old_code_valid, self.new_code_indent_issue)
        assert not success
        assert "E999" in errors
        assert "return 42" in errors

    def test_lint_wrong_var_issue(self, linter):
        success, errors = linter.lint_code_diff(self.old_code_valid, self.new_code_wrong_var_issue)
        assert not success
        assert "F821" in errors
        assert "return some" in errors

    def test_same_content(self, linter):
        success, errors = linter.lint_code_diff(self.old_code_valid, self.new_code_valid)
        assert success
        assert not errors

    old_code_with_error = """
def foo():
    return some
"""

    new_code_with_old_error = """
def foo():
    return some

def bar():
    return 42
"""

    def test_ignore_existing_errors(self, linter):
        success, errors = linter.lint_code_diff(self.old_code_with_error, self.new_code_with_old_error)
        assert success
        assert not errors

    @patch.object(PythonLinter, '_run_flake8_cli')
    def test_exception_in_flake_cli(self, mock_run_flake8_cli):
        mock_run_flake8_cli.side_effect = subprocess.SubprocessError("Mocked error")
        linter = PythonLinter("E999")
        content = "some code"
        result = linter.lint_single_code("some code")
        assert result == {}
        mock_run_flake8_cli.assert_called_once_with(content, linter.error_codes)

    @patch.object(PythonLinter, '_run_flake8_cli')
    def test_flake_cli_return_unexpected_string(self, mock_run_flake8_cli):
        mock_run_flake8_cli.return_value = ["not_int:some error"]
        linter = PythonLinter("E999")
        content = "some code"
        result = linter.lint_single_code("some code")
        assert result == {}
        mock_run_flake8_cli.assert_called_once_with(content, linter.error_codes)
