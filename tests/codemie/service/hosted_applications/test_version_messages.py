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

"""Owners see why a version was rejected, without the report ever quoting artifact content."""

import pytest

from codemie.core.hosted_applications.errors import ARTIFACT_REJECTION_CODES
from codemie.hosted_apps_validator.contract import LIMIT_REJECTION_CODES
from codemie.service.hosted_applications.versions import ERROR_MESSAGES, error_message
from codemie.service.hosted_applications.worker import RETRYABLE_ERROR_CODES

GENERIC = "This artifact did not pass validation"


@pytest.mark.parametrize("code", sorted(ARTIFACT_REJECTION_CODES | LIMIT_REJECTION_CODES))
def test_every_artifact_rejection_has_its_own_guidance(code):
    message = error_message(code)
    assert message != GENERIC
    assert "{" not in message and "}" not in message


def test_limit_guidance_states_the_policy_limits():
    assert "20 MiB for HTML" in error_message("UPLOAD_TOO_LARGE")
    assert "100 MiB for a ZIP" in error_message("ARTIFACT_TOO_LARGE")
    assert "5,000 files" in error_message("ARCHIVE_FILE_LIMIT")
    expansion = error_message("ARCHIVE_EXPANSION_LIMIT")
    assert "50 MiB per file" in expansion and "500 MiB in total" in expansion and "100 times" in expansion


def test_unknown_and_temporary_codes_keep_their_generic_wording():
    assert error_message("SOMETHING_NEW") == GENERIC
    # Retryable codes without their own guidance (POLICY_CHANGED has one) read as temporary.
    for code in RETRYABLE_ERROR_CODES - ERROR_MESSAGES.keys():
        assert "temporary platform condition" in error_message(code)
    assert not ARTIFACT_REJECTION_CODES & RETRYABLE_ERROR_CODES
    assert not LIMIT_REJECTION_CODES & (RETRYABLE_ERROR_CODES | ARTIFACT_REJECTION_CODES)


def test_scanner_limits_are_never_reported_as_malware():
    message = error_message("SCAN_LIMIT_EXCEEDED")
    assert "malware" in message.lower() and "flagged" not in message and "limits" in message
