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

"""Tests for ES index name sanitization (EPMCDME-11324).

Covers:
- sanitize_es_index_name(): standalone helper
- KnowledgeBase.get_identifier(): delegates to helper (no regression)
- GitRepo.identifier_from_fields(): email/mixed-case app_id sanitization
"""

import pytest

from codemie.core.constants import CodeIndexType
from codemie.core.models import GitRepo, KnowledgeBase, sanitize_es_index_name


class TestSanitizeEsIndexName:
    def test_lowercase_conversion(self):
        assert sanitize_es_index_name("MyProject") == "myproject"

    def test_email_like_name_lowercased(self):
        result = sanitize_es_index_name("Kostiantyn.Khomenko@medecision.com-awf-rules-code")
        assert result == "kostiantyn.khomenko@medecision.com-awf-rules-code"

    def test_already_lowercase_unchanged(self):
        assert sanitize_es_index_name("my-project-code") == "my-project-code"

    @pytest.mark.parametrize(
        "char",
        ['"', ' ', '\\', '/', ',', '|', '>', '?', '*', '<', ':', '#'],
    )
    def test_invalid_chars_replaced_with_underscore(self, char: str):
        result = sanitize_es_index_name(f"prefix{char}suffix")
        assert result == "prefix_suffix"

    def test_multiple_invalid_chars(self):
        # 'My Project: "test"' → lower → 'my project: "test"'
        # ' '→'_', '"'→'_', ':'→'_' gives 'my_project___test_'
        result = sanitize_es_index_name('My Project: "test"')
        assert result == "my_project___test_"

    def test_empty_string(self):
        assert sanitize_es_index_name("") == ""

    def test_all_valid_chars_unchanged(self):
        name = "valid-index_name.with@email"
        assert sanitize_es_index_name(name) == name


class TestKnowledgeBaseGetIdentifier:
    def test_lowercase_conversion(self):
        kb = KnowledgeBase(name="MyKnowledgeBase", type="knowledge_base")
        assert kb.get_identifier() == "myknowledgebase"

    def test_invalid_chars_replaced(self):
        kb = KnowledgeBase(name='test "name"', type="knowledge_base")
        assert kb.get_identifier() == "test__name_"

    def test_already_valid_name_unchanged(self):
        kb = KnowledgeBase(name="my-project-kb", type="knowledge_base")
        assert kb.get_identifier() == "my-project-kb"

    def test_project_scoped_name(self):
        kb = KnowledgeBase(name="myproject-myrepo", type="knowledge_base_code")
        assert kb.get_identifier() == "myproject-myrepo"

    def test_mixed_case_project_name(self):
        kb = KnowledgeBase(name="MyProject-MyRepo", type="knowledge_base_code")
        assert kb.get_identifier() == "myproject-myrepo"


class TestGitRepoIdentifierFromFields:
    def test_lowercase_mixed_case_app_id(self):
        result = GitRepo.identifier_from_fields("MyProject", "my-repo", CodeIndexType.CODE)
        assert result == "myproject-my-repo-code"

    def test_email_like_app_id(self):
        result = GitRepo.identifier_from_fields("Kostiantyn.Khomenko@medecision.com", "awf-rules", CodeIndexType.CODE)
        assert result == "kostiantyn.khomenko@medecision.com-awf-rules-code"

    def test_all_lowercase_app_id_unchanged(self):
        result = GitRepo.identifier_from_fields("myapp", "my-repo", CodeIndexType.CODE)
        assert result == "myapp-my-repo-code"

    def test_space_in_app_id_replaced(self):
        result = GitRepo.identifier_from_fields("My App", "repo", CodeIndexType.CODE)
        assert result == "my_app-repo-code"

    def test_summary_index_type(self):
        result = GitRepo.identifier_from_fields("MyApp", "repo", CodeIndexType.SUMMARY)
        assert result == "myapp-repo-summary"
