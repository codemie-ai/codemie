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
from pydantic import ValidationError

from codemie.rest_api.models.index import IndexInfo, IndexTypeByContextTypeMapping, XWikiIndexInfo


def test_defaults_to_the_main_wiki():
    info = XWikiIndexInfo(space="KB")
    assert info.space == "KB"
    assert info.wiki == "xwiki"


def test_space_is_required():
    with pytest.raises(ValidationError):
        XWikiIndexInfo()


def test_index_info_exposes_an_xwiki_field():
    assert "xwiki" in IndexInfo.model_fields


def test_xwiki_is_a_knowledge_base_context_type():
    assert "knowledge_base_xwiki" in IndexTypeByContextTypeMapping.KNOWLEDGE_BASE.value
