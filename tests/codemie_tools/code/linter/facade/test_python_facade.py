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

from codemie_tools.code.linter.facade import LinterFacade


class TestLinterFacade:
    @pytest.fixture
    def linter_facade(self):
        return LinterFacade()

    python_old_code_valid = """
def foo():
    return 42
"""

    python_new_code_indent_issue = """
def foo():
    return 42

def bar():
return 42
"""

    def test_lint_code_no_issues(self, linter_facade):
        success, errors = linter_facade.lint_code(
            'python', self.python_old_code_valid, self.python_new_code_indent_issue
        )
        assert not success
        assert "E999" in errors
        assert "return 42" in errors

    def test_unsupported_language(self, linter_facade):
        success, errors = linter_facade.lint_code('go', "some go code", "some new go code")
        assert success
        assert not errors
