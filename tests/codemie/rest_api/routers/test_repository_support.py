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

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.index import IndexInfo, UpdateIndexRequest
from codemie.rest_api.routers.index._repository_support import _update_svn_index_and_repo_fields
from codemie.rest_api.security.user import User


@pytest.fixture
def svn_index() -> IndexInfo:
    return IndexInfo(
        project_name="p",
        repo_name="r",
        index_type="code",
        vcs_type="svn",
        id="svn-index",
        setting_id="old",
    )


@pytest.fixture
def svn_repo() -> MagicMock:
    repo = MagicMock()
    repo.setting_id = "old"
    return repo


def _update(index: IndexInfo, svn_repo: MagicMock, setting_id: str | None) -> None:
    with patch("codemie.rest_api.models.base.BaseModelWithSQLSupport.update"):
        _update_svn_index_and_repo_fields(
            index_info=index,
            request=UpdateIndexRequest(name="r", setting_id=setting_id),
            user=MagicMock(spec=User),
            svn_repo=svn_repo,
        )


def test_update_svn_fields_empty_setting_id_clears_index_and_repo(svn_index: IndexInfo, svn_repo: MagicMock) -> None:
    _update(svn_index, svn_repo, "")

    assert svn_index.setting_id is None
    assert not svn_repo.setting_id


def test_update_svn_fields_none_setting_id_keeps_integration(svn_index: IndexInfo, svn_repo: MagicMock) -> None:
    _update(svn_index, svn_repo, None)

    assert svn_index.setting_id == "old"
    assert svn_repo.setting_id == "old"
