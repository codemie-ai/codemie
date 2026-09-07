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

from codemie.datasource.loader.xwiki_loader import XWikiLoader
from codemie_tools.core.project_management.xwiki.models import XWikiConfig


@pytest.fixture
def loader():
    return XWikiLoader(
        config=XWikiConfig(url="http://xwiki:8080/", username="AdminAdmin", token="admin"),
        space="KB",
    )


def test_rest_path_repeats_the_spaces_segment(loader):
    assert loader._rest_space_path("KB.Onboarding") == "/spaces/KB/spaces/Onboarding"


def test_bin_path_uses_plain_slashes(loader):
    assert loader._bin_page_path("KB.Onboarding", "Checklist") == "/bin/get/KB/Onboarding/Checklist"


def test_spaces_in_names_are_percent_encoded(loader):
    assert loader._bin_page_path("KB", "Vacation Policy") == "/bin/get/KB/Vacation%20Policy"


def test_rest_path_percent_encodes_too(loader):
    assert loader._rest_space_path("KB.Vacation Policy") == "/spaces/KB/spaces/Vacation%20Policy"


def test_cyrillic_is_percent_encoded(loader):
    expected = "/bin/get/KB/%D0%94%D0%BE%D0%B2%D1%96%D0%B4%D0%BA%D0%B0"
    assert loader._bin_page_path("KB", "Довідка") == expected


def test_base_url_trailing_slash_does_not_double(loader):
    assert loader._base == "http://xwiki:8080"
