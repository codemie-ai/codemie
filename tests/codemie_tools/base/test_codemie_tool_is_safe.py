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

import pytest
from codemie_tools.base.codemie_tool import CodeMieTool


class _Concrete(CodeMieTool):
    name: str = "concrete"
    description: str = "test"

    def execute(self, *args, **kwargs):
        return "ok"


@pytest.fixture
def tool():
    return _Concrete()


# --- CodeMieTool.is_safe default ---


def test_default_is_safe_returns_false(tool):
    assert tool.is_safe({}) is False


def test_default_is_safe_returns_false_with_get_method(tool):
    assert tool.is_safe({"method": "GET"}) is False


# --- _http_method_is_safe helper ---


def test_http_method_is_safe_get_returns_true():
    assert CodeMieTool._http_method_is_safe({"method": "GET"}) is True


def test_http_method_is_safe_head_returns_true():
    assert CodeMieTool._http_method_is_safe({"method": "HEAD"}) is True


def test_http_method_is_safe_options_returns_true():
    assert CodeMieTool._http_method_is_safe({"method": "OPTIONS"}) is True


def test_http_method_is_safe_post_returns_false():
    assert CodeMieTool._http_method_is_safe({"method": "POST"}) is False


def test_http_method_is_safe_put_returns_false():
    assert CodeMieTool._http_method_is_safe({"method": "PUT"}) is False


def test_http_method_is_safe_delete_returns_false():
    assert CodeMieTool._http_method_is_safe({"method": "DELETE"}) is False


def test_http_method_is_safe_missing_key_returns_false():
    assert CodeMieTool._http_method_is_safe({}) is False


def test_http_method_is_safe_none_value_returns_false():
    assert CodeMieTool._http_method_is_safe({"method": None}) is False


def test_http_method_is_safe_lowercase_get_returns_true():
    assert CodeMieTool._http_method_is_safe({"method": "get"}) is True


def test_http_method_is_safe_custom_key():
    assert CodeMieTool._http_method_is_safe({"verb": "GET"}, method_key="verb") is True


# --- CodeMieTool.written_refs default (EPMCDME-16835) ---


def test_default_written_refs_cannot_tell_for_a_mutating_call(tool):
    assert tool.written_refs({"method": "POST"}, '{"key": "PROJ-1"}') is None


def test_default_written_refs_is_empty_for_a_safe_call():
    class _SafeTool(_Concrete):
        def is_safe(self, args: dict) -> bool:
            return True

    assert _SafeTool().written_refs({}, '{"key": "PROJ-1"}') == ()
