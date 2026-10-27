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


from codemie_tools.base import image_constants
from codemie_tools.core.project_management.jira import tools as jira_tools


def test_image_mime_types_are_the_supported_raster_formats():
    expected = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    assert expected == image_constants.IMAGE_MIME_TYPES


def test_max_image_size_is_five_megabytes():
    assert image_constants.MAX_IMAGE_SIZE_BYTES == 5 * 1024 * 1024


def test_jira_reuses_shared_constants():
    assert jira_tools.IMAGE_MIME_TYPES is image_constants.IMAGE_MIME_TYPES
    assert jira_tools.MAX_IMAGE_SIZE_BYTES == image_constants.MAX_IMAGE_SIZE_BYTES
