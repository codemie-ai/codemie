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

"""UserRemapService tests (EPMCDME-16560)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from codemie.service.user.user_remap_service import UserRemapService

_MOD = "codemie.service.user.user_remap_service"


def _remap_event(remapped):
    actor = SimpleNamespace(id="root-1", email="root@example.com", name="Root")
    with (
        patch.object(UserRemapService, "_lock_old_user_row", return_value=True),
        patch.object(UserRemapService, "_preflight_check_conflicts"),
        patch.object(UserRemapService, "_remap_core_row_and_true_fk_tables"),
        patch.object(UserRemapService, "_update_plain_id_columns"),
        patch.object(UserRemapService, "_update_fk_columns_referencing_users_id"),
        patch.object(UserRemapService, "_update_created_by_jsonb"),
        patch(f"{_MOD}.user_repository") as repo,
        patch(f"{_MOD}.activity_event_repository") as activity,
    ):
        repo.get_by_id.return_value = remapped
        UserRemapService.remap_user(MagicMock(), "old-id", "new-id", actor)
    return activity.insert.call_args.args[0]


def test_user_remapped_event_carries_the_target_users_name_as_label():
    event = _remap_event(SimpleNamespace(id="new-id", name="Alice", email="alice@example.com"))

    assert (event.entity_type, event.entity_id, event.entity_label) == ("user", "new-id", "Alice")


def test_user_remapped_event_label_falls_back_to_email_without_a_name():
    event = _remap_event(SimpleNamespace(id="new-id", name=None, email="alice@example.com"))

    assert event.entity_label == "alice@example.com"
