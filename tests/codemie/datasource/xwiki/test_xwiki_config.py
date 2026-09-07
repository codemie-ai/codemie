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

from codemie.core.constants import DatasourceTypes
from codemie.service.constants import FullDatasourceTypes


def test_datasource_type_members_exist():
    assert DatasourceTypes.XWIKI.value == "xwiki"
    assert FullDatasourceTypes.XWIKI.value == "knowledge_base_xwiki"


def test_xwiki_loader_config_is_loaded_from_yaml():
    from codemie.datasource.datasources_config import XWIKI_CONFIG

    assert XWIKI_CONFIG.chunk_size > 0
    assert XWIKI_CONFIG.chunk_overlap >= 0
    assert XWIKI_CONFIG.loader_batch_size > 0
    assert XWIKI_CONFIG.loader_max_pages > 0
    assert XWIKI_CONFIG.request_timeout_seconds > 0
    assert XWIKI_CONFIG.max_failed_pages_floor >= 0
    assert 0 < XWIKI_CONFIG.max_failed_pages_ratio <= 1
