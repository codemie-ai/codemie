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

import json

import pytest

from codemie.workflows.utils.json_utils import UnwrappingJsonPointerEvaluator


@pytest.fixture
def inner_obj():
    return {'inner_node': {'final_node': [1, 2, 3]}}


@pytest.fixture
def source_json(inner_obj):
    outer_obj_with_serialized_inner = {'outer': {'outer_node': json.dumps(inner_obj)}}
    return outer_obj_with_serialized_inner


@pytest.fixture
def json_with_unparsable_child():
    return {'outer': {'outer_node': 'some garbage here'}}


class TestUnwrappingJsonPointer:
    def test_traverses_embedded_string(self, source_json):
        path = '/outer/outer_node/inner_node/final_node'
        result = UnwrappingJsonPointerEvaluator.get_node_by_pointer(source_json, path)
        expected = [1, 2, 3]
        assert result == expected

    def test_returns_verbatim_without_trailing_slash(self, source_json, inner_obj):
        path = '/outer/outer_node'
        result = UnwrappingJsonPointerEvaluator.get_node_by_pointer(source_json, path)
        expected = json.dumps(inner_obj)
        assert result == expected

    def test_returns_json_with_trailing_slash(self, source_json, inner_obj):
        path = '/outer/outer_node/'
        result = UnwrappingJsonPointerEvaluator.get_node_by_pointer(source_json, path)
        expected = inner_obj
        assert result == expected

    def test_array_addressing(self, source_json):
        path = '/outer/outer_node/inner_node/final_node/0'
        result = UnwrappingJsonPointerEvaluator.get_node_by_pointer(source_json, path)
        expected = 1
        assert result == expected

    def test_invalid_path(self, source_json):
        path = '/outer/non_existent_node'
        with pytest.raises(KeyError):
            UnwrappingJsonPointerEvaluator.get_node_by_pointer(source_json, path)

    def test_invalid_json_string(self, json_with_unparsable_child):
        path = '/outer/outer_node/inner_node/'
        with pytest.raises(ValueError):
            UnwrappingJsonPointerEvaluator.get_node_by_pointer(json_with_unparsable_child, path)
