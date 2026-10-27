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

"""The approval gate is armed only when marketplace management is on too (EPMCDME-14234)."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.configs import marketplace_management as mm

MODULE = "codemie.configs.marketplace_management"


@pytest.mark.parametrize(
    ("gate_flag", "management", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_armed_requires_both_switches(gate_flag, management, expected):
    with (
        patch.object(mm.config, "MARKETPLACE_APPROVAL_GATE_ENABLED", gate_flag),
        patch(f"{MODULE}.is_marketplace_management_enabled", return_value=management),
    ):
        assert mm.is_approval_gate_armed() is expected


def test_gate_off_skips_management_lookup():
    with (
        patch.object(mm.config, "MARKETPLACE_APPROVAL_GATE_ENABLED", False),
        patch(f"{MODULE}.is_marketplace_management_enabled") as lookup,
    ):
        assert mm.is_approval_gate_armed() is False
    lookup.assert_not_called()


class TestStartupWarning:
    def test_warns_when_gate_set_but_management_off(self):
        with (
            patch.object(mm.config, "MARKETPLACE_APPROVAL_GATE_ENABLED", True),
            patch.object(mm.config, "MARKETPLACE_MANAGEMENT_ENABLED", False),
            patch.object(mm, "_logger") as log,
        ):
            mm.warn_marketplace_approval_gate_enabled()
        log.warning.assert_called_once()
        assert "MARKETPLACE_MANAGEMENT_ENABLED=False" in log.warning.call_args.args[0]

    def test_warns_when_armed_without_moderator(self):
        repo = MagicMock()
        repo.has_any_moderator.return_value = False
        with (
            patch(f"{MODULE}.is_approval_gate_armed", return_value=True),
            patch.object(mm.config, "MARKETPLACE_MANAGEMENT_ENABLED", True),
            patch.dict(sys.modules, {"codemie.service.user.user_repository": SimpleNamespace(user_repository=repo)}),
            patch("codemie.clients.postgres.PostgresClient.get_engine"),
            patch("sqlmodel.Session"),
            patch.object(mm, "_logger") as log,
        ):
            mm.warn_marketplace_approval_gate_enabled()
        log.warning.assert_called_once()
        assert "no moderator" in log.warning.call_args.args[0]

    def test_silent_when_armed_with_moderator(self):
        repo = MagicMock()
        repo.has_any_moderator.return_value = True
        with (
            patch(f"{MODULE}.is_approval_gate_armed", return_value=True),
            patch.object(mm.config, "MARKETPLACE_MANAGEMENT_ENABLED", True),
            patch.dict(sys.modules, {"codemie.service.user.user_repository": SimpleNamespace(user_repository=repo)}),
            patch("codemie.clients.postgres.PostgresClient.get_engine"),
            patch("sqlmodel.Session"),
            patch.object(mm, "_logger") as log,
        ):
            mm.warn_marketplace_approval_gate_enabled()
        log.warning.assert_not_called()

    def test_silent_when_gate_off(self):
        with (
            patch.object(mm.config, "MARKETPLACE_APPROVAL_GATE_ENABLED", False),
            patch(f"{MODULE}.is_approval_gate_armed", return_value=False),
            patch.object(mm, "_logger") as log,
        ):
            mm.warn_marketplace_approval_gate_enabled()
        log.warning.assert_not_called()
