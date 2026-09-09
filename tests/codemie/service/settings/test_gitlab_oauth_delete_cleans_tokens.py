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

"""Deleting a GitLab OAuth integration invalidates all user tokens in TMS."""

import pytest

from codemie.core.exceptions import ExtendedHTTPException  # noqa: E402
from codemie.service.settings.settings import SettingsService  # noqa: E402


def test_cleanup_invalidates_tokens_for_integration():
    with pytest.MonkeyPatch.context() as monkeypatch:
        calls = []
        monkeypatch.setattr(
            "codemie.service.oauth.token_port.ToolOAuthTokenPort.invalidate_by_integration",
            lambda integration_id: calls.append(integration_id),
        )
        SettingsService._cleanup_gitlab_oauth_tokens("s1")
    assert calls == ["s1"]


def test_cleanup_maps_tms_failure_to_http_exception():
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "codemie.service.oauth.token_port.ToolOAuthTokenPort.invalidate_by_integration",
            lambda integration_id: (_ for _ in ()).throw(RuntimeError("tms down")),
        )
        with pytest.raises(ExtendedHTTPException):
            SettingsService._cleanup_gitlab_oauth_tokens("s1")
