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

from unittest.mock import patch

import pytest

from codemie.datasource.loader.xwiki_loader import XWikiLoader
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080", username="AdminAdmin", token="admin"),
        space="KB",
        page_size=2,
    )


def _space(space_id):
    return {"id": space_id}


def test_only_the_target_space_and_its_descendants_are_kept(loader):
    payload = {
        "spaces": [
            _space("xwiki:KB"),
            _space("xwiki:KB.Onboarding"),
            _space("xwiki:KBase"),  # prefix lookalike, must NOT match
            _space("xwiki:Main"),
        ]
    }
    with patch.object(loader, "_get_json", side_effect=[payload, {"spaces": []}]):
        assert list(loader._iter_spaces()) == ["KB", "KB.Onboarding"]


def test_spaces_of_another_wiki_are_ignored(loader):
    payload = {"spaces": [_space("xwiki:KB"), _space("other:KB.Secret")]}
    with patch.object(loader, "_get_json", side_effect=[payload, {"spaces": []}]):
        assert list(loader._iter_spaces()) == ["KB"]


def test_space_listing_is_paginated(loader):
    first = {"spaces": [_space("xwiki:KB"), _space("xwiki:KB.A")]}
    second = {"spaces": [_space("xwiki:KB.B")]}
    with patch.object(loader, "_get_json", side_effect=[first, second]) as get_json:
        assert list(loader._iter_spaces()) == ["KB", "KB.A", "KB.B"]
    assert get_json.call_count == 2
    assert get_json.call_args_list[1].kwargs["params"]["start"] == 2


def test_page_listing_stops_on_a_short_page(loader):
    first = {"pageSummaries": [{"name": "A"}, {"name": "B"}]}
    second = {"pageSummaries": [{"name": "C"}]}
    with patch.object(loader, "_get_json", side_effect=[first, second]) as get_json:
        names = [p["name"] for p in loader._iter_page_summaries("KB")]
    assert names == ["A", "B", "C"]
    assert get_json.call_count == 2


def test_page_listing_uses_the_nested_rest_path(loader):
    with patch.object(loader, "_get_json", return_value={"pageSummaries": []}) as get_json:
        list(loader._iter_page_summaries("KB.Onboarding"))
    assert get_json.call_args.args[0] == "/rest/wikis/xwiki/spaces/KB/spaces/Onboarding/pages"
