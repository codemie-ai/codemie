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

from codemie.service.settings.settings import SettingsService
from codemie.service.settings.settings_tester import SettingsTester
from codemie_tools.base.models import CredentialTypes


def _tester():
    tester = SettingsTester.__new__(SettingsTester)
    tester.credential_type = CredentialTypes.XWIKI
    tester.credential_values = {
        "url": "http://xwiki:8080",
        "username": "AdminAdmin",
        "token": "admin",
    }
    return tester


def test_a_handler_is_registered_for_xwiki():
    assert CredentialTypes.XWIKI in _tester().handlers


def test_success_message_states_only_what_was_verified():
    """xWiki grants guest read by default, so a 200 does not prove the password is right."""
    tester = _tester()
    with patch("codemie.service.settings.settings_tester.ListWikisTool") as tool:
        tool.return_value.healthcheck.return_value = (True, "ok")
        ok, message = tester.test()
    assert ok is True
    assert "credentials accepted" in message.lower()
    assert "guest" in message.lower()


def test_failure_message_is_passed_through_unchanged():
    tester = _tester()
    with patch("codemie.service.settings.settings_tester.ListWikisTool") as tool:
        tool.return_value.healthcheck.return_value = (False, "connection refused")
        ok, message = tester.test()
    assert ok is False
    assert message == "connection refused"


def test_get_xwiki_creds_resolves_through_get_config():
    with patch.object(SettingsService, "get_config", return_value=MagicMock()) as get_config:
        SettingsService.get_xwiki_creds(user_id="u1", project_name="p1")
    assert get_config.call_args.kwargs["user_id"] == "u1"
    assert get_config.call_args.kwargs["project_name"] == "p1"
