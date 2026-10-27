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

"""What `_publish_single_sub_assistant` actually SAVES, with a real Assistant (EPMCDME-14234).

The sibling tests use MagicMock sub-assistants, whose status never matches `_SUBMITTABLE_STATUSES`, so
they never reached the branch that moves `marketplace_status` with `is_global`. With the approval gate off
that branch set both fields in memory *after* its only save: the sub-assistant stayed private while the
parent's response said it was published. These tests record the values at the moment each write happens.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.assistant import Assistant, SubAssistantPublishSettings
from codemie.rest_api.routers.assistant import _sub_assistants as subs


class _Recorder:
    """Stands in for the database: `refresh` reloads stored values, `update` records what is saved."""

    def __init__(self, stored_status: str):
        self.stored_status = stored_status
        self.saves: list[tuple[bool, str]] = []

    def install(self, sub: Assistant):
        def refresh(this):
            # What the DB holds: the committed marketplace columns overwrite whatever is in memory.
            this.marketplace_status = self.stored_status
            this.is_global = False

        def update(this, refresh=False):  # noqa: ARG001 - mirrors Assistant.update's signature
            self.saves.append((this.is_global, this.marketplace_status))

        return (
            patch.object(Assistant, "refresh_marketplace_columns_from_db", autospec=True, side_effect=refresh),
            patch.object(Assistant, "update", autospec=True, side_effect=update),
        )


def _sub(status: str, is_global: bool = False) -> Assistant:
    return Assistant(
        id="sub-1",
        name="Sub",
        description="d",
        system_prompt="p",
        categories=["cat-1"],
        marketplace_status=status,
        is_global=is_global,
    )


def _publish(sub, recorder, *, gate_armed, admin_skip_queue=False, settings=None):
    refresh_patch, update_patch = recorder.install(sub)
    with (
        refresh_patch,
        update_patch,
        patch.object(subs, "_track_assistant_management_metric"),
        patch.object(subs, "_submit_for_review") as submit,
        patch.object(subs, "_force_publish_assistant") as force,
        patch.object(subs, "_publish_gate_findings", return_value=[]),
    ):
        result = subs._publish_single_sub_assistant(
            "sub-1",
            sub,
            {"sub-1": settings} if settings else {},
            MagicMock(id="parent"),
            MagicMock(),
            gate_armed,
            admin_skip_queue,
            None,
        )
    return result, submit, force


@pytest.mark.parametrize("status", ["DRAFT", "REJECTED", "WITHDRAWN"])
class TestGateOff:
    def test_sub_assistant_is_saved_public_and_approved(self, status):
        sub = _sub(status)
        recorder = _Recorder(status)
        result, _, _ = _publish(sub, recorder, gate_armed=False)

        assert result is True
        assert recorder.saves, "the sub-assistant was never saved"
        # The LAST write is what is persisted: it must carry the published state.
        assert recorder.saves[-1] == (True, "APPROVED")

    def test_nothing_is_recorded_in_the_moderation_queue(self, status):
        _, submit, force = _publish(_sub(status), _Recorder(status), gate_armed=False)

        submit.assert_not_called()
        force.assert_not_called()

    def test_values_set_survive_the_refresh_from_the_database(self, status):
        """The refresh overwrites in-memory marketplace columns, so it must run before they are set."""
        sub = _sub(status)
        recorder = _Recorder(status)
        _publish(sub, recorder, gate_armed=False)

        assert recorder.saves[-1][0] is True
        assert (sub.is_global, sub.marketplace_status) == (True, "APPROVED")

    def test_requested_private_is_saved_private(self, status):
        sub = _sub(status)
        recorder = _Recorder(status)
        result, _, _ = _publish(
            sub, recorder, gate_armed=False, settings=SubAssistantPublishSettings(assistant_id="sub-1", is_global=False)
        )

        assert result is False
        assert recorder.saves[-1][0] is False


class TestGateArmed:
    def test_regular_user_submits_for_review_and_stays_private(self):
        sub = _sub("DRAFT")
        recorder = _Recorder("DRAFT")
        result, submit, force = _publish(sub, recorder, gate_armed=True, admin_skip_queue=False)

        assert result is False
        submit.assert_called_once()
        force.assert_not_called()
        assert sub.marketplace_status == "PENDING_REVIEW"
        assert sub.is_global is False

    def test_admin_skip_queue_force_publishes(self):
        sub = _sub("DRAFT")
        recorder = _Recorder("DRAFT")
        result, submit, force = _publish(sub, recorder, gate_armed=True, admin_skip_queue=True)

        assert result is True
        force.assert_called_once()
        submit.assert_not_called()
        assert (sub.is_global, sub.marketplace_status) == (True, "APPROVED")

    def test_settings_are_saved_before_the_queue_decision(self):
        """The compare-and-set writes the marketplace columns itself, so the save has to come first."""
        order: list[str] = []
        sub = _sub("DRAFT")
        recorder = _Recorder("DRAFT")
        refresh_patch, _ = recorder.install(sub)
        with (
            refresh_patch,
            patch.object(
                Assistant, "update", autospec=True, side_effect=lambda this, refresh=False: order.append("save")
            ),
            patch.object(subs, "_track_assistant_management_metric"),
            patch.object(subs, "_submit_for_review", side_effect=lambda *a, **k: order.append("cas")),
            patch.object(subs, "_publish_gate_findings", return_value=[]),
        ):
            subs._publish_single_sub_assistant("sub-1", sub, {}, MagicMock(id="p"), MagicMock(), True, False, None)

        assert order == ["save", "cas"]


@pytest.mark.parametrize("status", ["PENDING_REVIEW", "APPROVED"])
def test_already_reviewed_sub_assistant_is_not_moved_through_the_queue(status):
    sub = _sub(status, is_global=(status == "APPROVED"))
    recorder = _Recorder(status)
    _, submit, force = _publish(sub, recorder, gate_armed=False)

    submit.assert_not_called()
    force.assert_not_called()
    assert recorder.saves, "the visibility change must still be saved"
