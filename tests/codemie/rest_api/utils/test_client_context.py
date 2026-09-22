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

import pytest

from codemie.rest_api.utils.client_context import (
    ClientSource,
    clear_client_source,
    get_client_source,
    normalize_client_source,
    set_client_source,
)


class TestNormalizeClientSource:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_absent_or_blank_defaults_to_platform(self, raw):
        assert normalize_client_source(raw) is ClientSource.PLATFORM

    @pytest.mark.parametrize("raw", ["teams-bot", "TEAMS-BOT", "  teams-bot  "])
    def test_recognized_teams_values_map_to_ms_teams_bot(self, raw):
        assert normalize_client_source(raw) is ClientSource.MS_TEAMS_BOT

    @pytest.mark.parametrize("raw", ["web", "WEB", "  web  "])
    def test_recognized_platform_values_map_to_platform(self, raw):
        assert normalize_client_source(raw) is ClientSource.PLATFORM

    @pytest.mark.parametrize("raw", ["chrome-extension", "Chrome-Extension", "  chrome-extension  "])
    def test_recognized_chrome_extension_values_map_to_chrome_extension(self, raw):
        assert normalize_client_source(raw) is ClientSource.CHROME_EXTENSION

    @pytest.mark.parametrize(
        "raw",
        [
            "sdk",
            "codemie-cli",
            "codemie-code",
            "codemie-daemon",
            "codemie-claude",
            "codemie-claude-acp",
            "codemie-gemini",
            "some-unknown-client",
            "TEAMSBOT",
            "teams",
            "msteams",
            "webapp",
            "desktop",
            "ui",
        ],
    )
    def test_unrecognized_values_map_to_other(self, raw):
        assert normalize_client_source(raw) is ClientSource.OTHER


class TestClientSourceContextVar:
    def test_default_is_platform(self):
        assert get_client_source() is ClientSource.PLATFORM

    def test_set_and_get(self):
        set_client_source(ClientSource.MS_TEAMS_BOT)
        assert get_client_source() is ClientSource.MS_TEAMS_BOT

    def test_clear_resets_to_platform_not_none(self):
        set_client_source(ClientSource.OTHER)
        clear_client_source()
        assert get_client_source() is ClientSource.PLATFORM
