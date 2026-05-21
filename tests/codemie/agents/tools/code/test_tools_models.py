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


from codemie.agents.tools.code.tools_models import SearchInput, SearchInputByPaths


class TestSearchInputKeywordsListValidator:
    def test_json_string_parsed_to_list(self):
        result = SearchInput.model_validate(
            {"keywords_list": '["TCO_Manager", "thread", "PD_", "background"]', "query": "test"}
        )
        assert result.keywords_list == ["TCO_Manager", "thread", "PD_", "background"]

    def test_python_repr_string_parsed_to_list(self):
        result = SearchInput.model_validate(
            {"keywords_list": "['TCO_Manager', 'thread', 'PD.', 'background']", "query": "test"}
        )
        assert result.keywords_list == ["TCO_Manager", "thread", "PD.", "background"]

    def test_proper_list_passed_through(self):
        result = SearchInput.model_validate({"keywords_list": ["a", "b", "c"], "query": "test"})
        assert result.keywords_list == ["a", "b", "c"]

    def test_none_returns_none(self):
        result = SearchInput.model_validate({"keywords_list": None, "query": "test"})
        assert result.keywords_list is None

    def test_empty_list_passed_through(self):
        result = SearchInput.model_validate({"keywords_list": [], "query": "test"})
        assert result.keywords_list == []

    def test_empty_json_array_string(self):
        result = SearchInput.model_validate({"keywords_list": "[]", "query": "test"})
        assert result.keywords_list == []

    def test_malformed_string_returns_empty_list(self):
        result = SearchInput.model_validate({"keywords_list": "not a list at all", "query": "test"})
        assert result.keywords_list == []

    def test_omitted_uses_default(self):
        result = SearchInput.model_validate({"query": "test"})
        assert result.keywords_list == []


class TestSearchInputFilePathValidator:
    def test_json_string_parsed_to_list(self):
        result = SearchInput.model_validate({"file_path": '["src/main.py", "src/utils.py"]', "query": "test"})
        assert result.file_path == ["src/main.py", "src/utils.py"]

    def test_python_repr_string_parsed_to_list(self):
        result = SearchInput.model_validate({"file_path": "['src/main.py', 'src/utils.py']", "query": "test"})
        assert result.file_path == ["src/main.py", "src/utils.py"]

    def test_proper_list_passed_through(self):
        result = SearchInput.model_validate({"file_path": ["a.py", "b.py"], "query": "test"})
        assert result.file_path == ["a.py", "b.py"]

    def test_malformed_string_returns_empty_list(self):
        result = SearchInput.model_validate({"file_path": "just/a/path", "query": "test"})
        assert result.file_path == []


class TestSearchInputByPathsInheritance:
    def test_inherits_keywords_list_validator(self):
        result = SearchInputByPaths.model_validate(
            {"keywords_list": '["a", "b"]', "query": "test", "file_path": '["x.py"]'}
        )
        assert result.keywords_list == ["a", "b"]
        assert result.file_path == ["x.py"]

    def test_limit_docs_count_field(self):
        result = SearchInputByPaths.model_validate({"query": "test", "limit_docs_count": 10})
        assert result.limit_docs_count == 10

    def test_limit_docs_count_default_none(self):
        result = SearchInputByPaths.model_validate({"query": "test"})
        assert result.limit_docs_count is None
