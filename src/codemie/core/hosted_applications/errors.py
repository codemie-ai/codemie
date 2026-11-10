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


from __future__ import annotations


class HostedApplicationError(Exception):
    """Public, bounded error; messages must never contain credentials or content."""

    def __init__(self, code: str, message: str, status: int = 422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


# One bounded public rejection vocabulary across parser, coordinator and management.
ARTIFACT_REJECTION_CODES = frozenset(
    {
        "MALWARE_DETECTED",
        "SECRET_DETECTED",
        "UNSUPPORTED_ARTIFACT",
        "INVALID_ENCODING",
        "UNSAFE_SVG",
        "INVALID_HTML",
        "UNSUPPORTED_ARCHIVE",
        "ARCHIVE_PATH_COLLISION",
        "ARCHIVE_FILE_LIMIT",
        "UPLOAD_TOO_LARGE",
        "ENTRYPOINT_REQUIRED",
        "INVALID_ARCHIVE",
        "INVALID_PATH",
        "INVALID_ARCHIVE_PATH",
        "ARCHIVE_EXPANSION_LIMIT",
        "ARTIFACT_TOO_LARGE",
        "ZIP_BOMB",
    }
)
