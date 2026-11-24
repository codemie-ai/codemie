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

"""The automatic cover picture: its stored name, media type and limits, shared by the validator and the API."""

from __future__ import annotations

# The picture is stored beside the validated files of its attempt.
PREVIEW_NAME = "preview.jpg"
PREVIEW_MIME = "image/jpeg"
MAX_PREVIEW_BYTES = 1024 * 1024
# The largest width and height of the picture, in pixels. There is no area limit.
MAX_PREVIEW_SIDE = 2048
