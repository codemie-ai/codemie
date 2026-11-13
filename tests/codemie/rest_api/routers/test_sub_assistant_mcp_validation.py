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

import pytest

from codemie.core.exceptions import ValidationException
from codemie.rest_api.models.assistant import MCPServerDetails, SubAssistantPublishSettings
from codemie.rest_api.routers.assistant import _sub_assistants as subs
from codemie.rest_api.routers.assistant._sub_assistants import _apply_sub_assistant_settings

ACCESS = "codemie.service.mcp.access_control"


def _settings(servers):
    return SubAssistantPublishSettings(assistant_id="sub1", mcp_servers=servers)


def _sub():
    sub = MagicMock()
    sub.mcp_servers = []
    return sub


def _open_mode():
    mode = MagicMock()
    mode.is_component_enabled.return_value = False
    return patch(f"{ACCESS}.customer_config", mode)


def _catalog(entries):
    return patch(f"{ACCESS}.MCPConfig.get_by_ids", return_value=entries)


def _entry(entry_id, active=True):
    return MagicMock(id=entry_id, is_active=active, is_public=True)


def _link(config_id, enabled=True):
    return MCPServerDetails(name=f"srv-{config_id}", mcp_config_id=config_id, enabled=enabled)


class TestApplySubAssistantSettingsMcpValidation:
    def test_assigns_without_validating_again(self):
        # The payload is validated for every sub-assistant before any save; the per-sub-assistant save step
        # must not read the catalogue again (a config deactivated in between would leave a partial publish).
        sub = _sub()
        servers = [_link("c1")]
        with patch(f"{ACCESS}.MCPConfig.get_by_ids") as lookup:
            _apply_sub_assistant_settings(sub, _settings(servers))
        lookup.assert_not_called()
        assert sub.mcp_servers == servers

    def test_disabled_leftover_passes(self):
        sub = _sub()
        servers = [_link("c1", enabled=False), _link("c2")]
        with _open_mode(), _catalog([_entry("c2")]):
            _apply_sub_assistant_settings(sub, _settings(servers))
        assert sub.mcp_servers == servers

    def test_omitted_mcp_servers_left_untouched(self):
        sub = _sub()
        sub.mcp_servers = ["keep"]
        _apply_sub_assistant_settings(sub, SubAssistantPublishSettings(assistant_id="sub1"))
        assert sub.mcp_servers == ["keep"]


class TestPublishSubAssistantsMcpValidationIsPreflight:
    @pytest.mark.parametrize(
        "servers, entries",
        [
            ([_link("c1")], [_entry("c1", active=False)]),
            ([_link("c1"), _link("c1")], [_entry("c1")]),
        ],
        ids=["dead_enabled_link", "duplicate_links"],
    )
    def test_invalid_payload_is_refused_before_any_save(self, servers, entries):
        parent = MagicMock(id="parent", assistant_ids=["sub1"])
        sub = MagicMock(id="sub1")
        with (
            _open_mode(),
            _catalog(entries),
            patch.object(subs, "_validate_sub_assistant", return_value=("sub1", sub)),
            patch.object(subs, "_publish_single_sub_assistant") as publish_single,
        ):
            with pytest.raises(ValidationException):
                subs._publish_sub_assistants(
                    parent,
                    [SubAssistantPublishSettings(assistant_id="sub1", mcp_servers=servers)],
                    MagicMock(),
                    False,
                    False,
                    None,
                )
        publish_single.assert_not_called()

    def test_invalid_second_sub_assistant_leaves_the_first_unsaved(self):
        parent = MagicMock(id="parent", assistant_ids=["sub1", "sub2"])
        settings = [
            SubAssistantPublishSettings(assistant_id="sub1", mcp_servers=[_link("c1")]),
            SubAssistantPublishSettings(assistant_id="sub2", mcp_servers=[_link("c2")]),
        ]
        sub_a, sub_b = MagicMock(id="sub1"), MagicMock(id="sub2")
        loaded = {"sub1": ("sub1", sub_a), "sub2": ("sub2", sub_b)}

        with (
            _open_mode(),
            _catalog([_entry("c1"), _entry("c2", active=False)]),
            patch.object(subs, "_validate_sub_assistant", side_effect=lambda sub_id, *_: loaded[sub_id]),
            patch.object(subs, "_publish_single_sub_assistant") as publish_single,
        ):
            with pytest.raises(ValidationException):
                subs._publish_sub_assistants(parent, settings, MagicMock(), False, False, None)

        publish_single.assert_not_called()
        sub_a.update.assert_not_called()
