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
from codemie.rest_api.models.assistant import Context, ContextType, IndexInfo
from codemie.core.constants import CodeIndexType


@pytest.mark.parametrize(
    "index_type, context_type, expected",
    [
        (ContextType.CODE, ContextType.CODE, ContextType.CODE),
        (ContextType.KNOWLEDGE_BASE, ContextType.KNOWLEDGE_BASE, ContextType.KNOWLEDGE_BASE),
        (CodeIndexType.SUMMARY, ContextType.CODE, ContextType.CODE),
        (CodeIndexType.CHUNK_SUMMARY, ContextType.CODE, ContextType.CODE),
    ],
)
def test_context_index_info_type(index_type, context_type, expected):
    index = IndexInfo(index_type=index_type, project_name='test_project', repo_name='test_repo')
    context = Context(context_type=context_type, name='Example Context')
    assert context.index_info_type(index) == expected
