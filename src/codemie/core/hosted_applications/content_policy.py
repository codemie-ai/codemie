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


"""Content policy shared by the content gateway and the preview renderer."""

from __future__ import annotations

# The first path segment the platform keeps for its own routes; no application file may use it.
RESERVED_PREFIX = "_platform"

CONTENT_CSP = (
    "default-src 'none'; base-uri 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
    "media-src 'self' blob:; connect-src 'self'; frame-src 'none'; object-src 'none'; "
    "worker-src 'none'; manifest-src 'none'; form-action 'none'; frame-ancestors 'none'; "
    "sandbox allow-scripts allow-same-origin allow-popups"
)
