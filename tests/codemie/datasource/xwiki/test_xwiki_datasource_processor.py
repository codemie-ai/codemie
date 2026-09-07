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

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from codemie.core.models import CreatedByUser
from codemie.datasource.exceptions import ConnectionException
from codemie.datasource.loader.xwiki_loader import (
    Metadata,
    XWikiLoader,
)
from codemie.datasource.xwiki.xwiki_datasource_processor import XWikiDatasourceProcessor
from codemie.rest_api.models.index import XWikiIndexInfo
from codemie.rest_api.security.user import User
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


@pytest.fixture
def processor():
    index_info = MagicMock()
    index_info.index_type = "knowledge_base_xwiki"
    index_info.created_by = CreatedByUser(id="1", username="testuser")
    index_info.xwiki = XWikiIndexInfo(space="KB", wiki="xwiki")
    index_info.update_date = datetime.now()
    return XWikiDatasourceProcessor(
        datasource_name="kb_ds",
        user=User(id="1", username="testuser"),
        project_name="test_project",
        credentials=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
        index_info=index_info,
        setting_id="setting_id",
    )


def test_index_name_combines_project_and_datasource(processor):
    assert processor._index_name == "test_project-kb_ds"


def test_index_type_constant(processor):
    assert processor.INDEX_TYPE == "knowledge_base_xwiki"


def test_loader_is_built_from_the_configured_space(processor):
    loader = processor._init_loader()
    assert isinstance(loader, XWikiLoader)
    assert loader.space == "KB"
    assert loader.wiki == "xwiki"


def test_process_chunk_preserves_every_metadata_key(processor):
    metadata = {
        Metadata.SOURCE.value: "http://xwiki:8080/bin/view/KB/Formatting",
        Metadata.PAGE_ID.value: "xwiki:KB.Formatting",
        Metadata.SPACE.value: "KB",
        Metadata.WIKI.value: "xwiki",
        Metadata.TITLE.value: "Formatting",
        Metadata.MODIFIED.value: 1785836295000,
        Metadata.VERSION.value: "1.1",
        Metadata.AUTHOR.value: "XWiki.AdminAdmin",
        Metadata.CONTENT_FORMAT.value: "rendered",
        "chunk_num": 2,
    }
    result = processor._process_chunk("chunk text", metadata, Document(page_content="x"))
    assert result.page_content == "chunk text"
    for key, value in metadata.items():
        if key != "chunk_num":
            assert result.metadata[key] == value


def test_splitter_uses_the_xwiki_chunk_settings(processor):
    from codemie.datasource.datasources_config import XWIKI_CONFIG

    splitter = XWikiDatasourceProcessor._get_splitter()
    assert splitter._chunk_size == XWIKI_CONFIG.chunk_size


def test_check_docs_health_returns_the_remote_count(processor):
    loader = MagicMock()
    loader.fetch_remote_stats.return_value = {XWikiLoader.DOCUMENTS_COUNT_KEY: 7}
    with patch.object(processor, "_init_loader", return_value=loader):
        assert processor._check_docs_health() == 7


def test_check_docs_health_propagates_errors_instead_of_reporting_zero(processor):
    loader = MagicMock()
    loader.fetch_remote_stats.side_effect = ConnectionException("xWiki", "boom")
    with patch.object(processor, "_init_loader", return_value=loader):
        with pytest.raises(ConnectionException):
            processor._check_docs_health()
