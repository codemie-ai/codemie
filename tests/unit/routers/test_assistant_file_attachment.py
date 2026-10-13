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

"""Unit tests for _validate_file_attachment_allowed_and_raise."""

from unittest.mock import MagicMock

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.assistant._ask import _validate_file_attachment_allowed_and_raise


class TestValidateFileAttachmentAllowed:
    def test_no_file_names_always_passes(self):
        assistant = MagicMock(file_attachment_enabled=False)
        _validate_file_attachment_allowed_and_raise(assistant, None)
        _validate_file_attachment_allowed_and_raise(assistant, [])

    def test_assistant_disabled_raises_403(self):
        assistant = MagicMock(file_attachment_enabled=False)
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])
        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_assistant_enabled_does_not_raise(self):
        assistant = MagicMock(file_attachment_enabled=True)
        _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])

    def test_assistant_not_configured_does_not_raise(self):
        assistant = MagicMock(file_attachment_enabled=None)
        _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])
