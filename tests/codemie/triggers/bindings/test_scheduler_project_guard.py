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

"""A scheduler must not run a target that now lives in another project (EPMCDME-13973)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.triggers.bindings import cron_project_guard
from codemie.triggers.bindings.cron import _run_unless_owner_inactive, _tracked_run, _tracked_run_sync

_REAL_PROJECT_MISMATCH = cron_project_guard.project_mismatch
_SETTINGS = "codemie.rest_api.models.settings.Settings.get_by_id"
_ASSISTANT_CHECK = "codemie.service.assistant.assistant_service.AssistantService.belongs_to_project"
_WORKFLOW_CHECK = "codemie.service.workflow_service.WorkflowService.belongs_to_project"
_DATASOURCE_CHECK = "codemie.service.index.index_service.IndexStatusService.belongs_to_project"
_SKIP_CONTENT = "The scheduled resource does not belong to the scheduler's project"


@pytest.fixture(autouse=True)
def _real_project_mismatch():
    """The bindings conftest stubs the check out; these tests exercise the real one."""
    with patch("codemie.triggers.bindings.cron_project_guard.project_mismatch", _REAL_PROJECT_MISMATCH):
        yield


@pytest.fixture
def scheduler_setting():
    setting = MagicMock()
    setting.project_name = "proj1"
    with patch(_SETTINGS, return_value=setting) as get_by_id:
        yield get_by_id


@pytest.fixture
def run_repo():
    with (
        patch("codemie.repository.scheduler_run_repository.SchedulerRunRepository") as mock_repo_cls,
        patch("codemie.rest_api.models.scheduler_run.SchedulerRun"),
    ):
        repo = mock_repo_cls.return_value
        repo.create.return_value = MagicMock(id="run-1")
        yield repo


@pytest.fixture
def log():
    with patch("codemie.triggers.bindings.cron_project_guard.logger") as mock_logger:
        yield mock_logger


def _assert_skipped(repo):
    repo.update.assert_called_once()
    fields = repo.update.call_args[0][1]
    assert fields["status"] == "skipped"
    assert fields["result_data"] == {"content": _SKIP_CONTENT}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("id_kwarg", "check"),
    [("assistant_id", _ASSISTANT_CHECK), ("workflow_id", _WORKFLOW_CHECK)],
    ids=["assistant", "workflow"],
)
async def test_run_is_skipped_when_target_left_scheduler_project(run_repo, scheduler_setting, log, id_kwarg, check):
    actor = AsyncMock()

    with patch(check, return_value=False) as belongs:
        result = await _tracked_run(actor, scheduler_id="sched-1", user_id="owner-1", **{id_kwarg: "res-1"})

    assert result is None
    belongs.assert_called_once_with("res-1", "proj1")
    actor.assert_not_called()
    _assert_skipped(run_repo)
    log.error.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("id_kwarg", "check"),
    [("assistant_id", _ASSISTANT_CHECK), ("workflow_id", _WORKFLOW_CHECK)],
    ids=["assistant", "workflow"],
)
async def test_run_goes_ahead_when_target_in_scheduler_project(run_repo, scheduler_setting, log, id_kwarg, check):
    actor = AsyncMock(return_value=None)

    with patch(check, return_value=True):
        await _tracked_run(actor, scheduler_id="sched-1", user_id="owner-1", **{id_kwarg: "res-1"})

    actor.assert_awaited_once()
    assert run_repo.update.call_args[0][1]["status"] == "completed"
    log.error.assert_not_called()


@pytest.mark.asyncio
async def test_run_is_skipped_when_scheduler_setting_is_missing(run_repo, log):
    actor = AsyncMock()

    with patch(_SETTINGS, side_effect=KeyError("gone")):
        result = await _tracked_run(actor, scheduler_id="sched-1", assistant_id="a-1", user_id="owner-1")

    assert result is None
    actor.assert_not_called()
    _assert_skipped(run_repo)


@pytest.mark.asyncio
async def test_run_is_skipped_when_target_id_is_absent(run_repo, scheduler_setting, log):
    actor = AsyncMock()

    result = await _tracked_run(actor, scheduler_id="sched-1", user_id="owner-1")

    assert result is None
    actor.assert_not_called()
    _assert_skipped(run_repo)


@pytest.mark.asyncio
async def test_run_is_skipped_when_target_lookup_returns_none(run_repo, scheduler_setting, log):
    actor = AsyncMock()

    with patch(_ASSISTANT_CHECK, return_value=None):
        result = await _tracked_run(actor, scheduler_id="sched-1", assistant_id="gone", user_id="owner-1")

    assert result is None
    actor.assert_not_called()
    _assert_skipped(run_repo)


def _payload(index_id="ds-1"):
    payload = MagicMock()
    payload.user.id = "owner-1"
    payload.index_info.id = index_id
    # Owner resolution copies the payload with the owner loaded; the copy stands in for the same payload here.
    payload.model_copy.return_value = payload
    return payload


def test_datasource_run_is_skipped_when_target_left_scheduler_project(run_repo, scheduler_setting, log):
    actor = MagicMock()

    with patch(_DATASOURCE_CHECK, return_value=False) as belongs:
        result = _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="sched-2", payload=_payload())

    assert result is None
    belongs.assert_called_once_with("ds-1", "proj1")
    actor.assert_not_called()
    _assert_skipped(run_repo)
    log.error.assert_called_once()


def test_datasource_run_goes_ahead_when_target_in_scheduler_project(run_repo, scheduler_setting, log):
    actor = MagicMock(return_value=None)

    with (
        patch(_DATASOURCE_CHECK, return_value=True),
        patch("codemie.triggers.bindings.cron._extract_datasource_metrics", return_value=None),
    ):
        _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="sched-2", payload=_payload())

    actor.assert_called_once()
    assert run_repo.update.call_args[0][1]["status"] == "completed"


@pytest.mark.parametrize(
    "payload",
    [MagicMock(index_info=None), MagicMock(index_info=MagicMock(id=None)), None],
    ids=["no-index-info", "no-index-id", "no-payload"],
)
def test_datasource_run_is_skipped_without_a_target_id(run_repo, scheduler_setting, log, payload):
    actor = MagicMock()
    kwargs = {} if payload is None else {"payload": payload}

    result = _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="sched-2", **kwargs)

    assert result is None
    actor.assert_not_called()
    _assert_skipped(run_repo)


def test_datasource_run_is_skipped_when_scheduler_setting_is_missing(run_repo, log):
    actor = MagicMock()

    with patch(_SETTINGS, side_effect=KeyError("gone")):
        _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="sched-2", payload=_payload())

    actor.assert_not_called()
    _assert_skipped(run_repo)


def test_datasource_run_is_skipped_when_datasource_is_missing(run_repo, scheduler_setting, log):
    actor = MagicMock()

    with patch(_DATASOURCE_CHECK, return_value=None):
        _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="sched-2", payload=_payload())

    actor.assert_not_called()
    _assert_skipped(run_repo)


def test_datasource_run_tolerates_a_mock_payload(run_repo, scheduler_setting, log):
    actor = MagicMock()

    with patch(_DATASOURCE_CHECK, return_value=False):
        _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="sched-2", payload=MagicMock())

    actor.assert_not_called()


def test_untracked_run_is_blocked_when_target_left_scheduler_project(scheduler_setting, log):
    actor = MagicMock()

    with patch(_DATASOURCE_CHECK, return_value=False):
        result = _run_unless_owner_inactive(actor, scheduler_id="sched-3", payload=_payload())

    assert result is None
    actor.assert_not_called()
    log.error.assert_called_once()


def test_untracked_run_goes_ahead_when_target_in_scheduler_project(scheduler_setting, log):
    actor = MagicMock(return_value="done")
    payload = _payload()

    with patch(_DATASOURCE_CHECK, return_value=True):
        result = _run_unless_owner_inactive(actor, scheduler_id="sched-3", payload=payload)

    assert result == "done"
    actor.assert_called_once_with(payload=payload)


@pytest.mark.parametrize(
    "payload",
    [MagicMock(index_info=None), MagicMock(index_info=MagicMock(id=None))],
    ids=["no-index-info", "no-index-id"],
)
def test_untracked_run_is_blocked_without_a_target_id(scheduler_setting, log, payload):
    actor = MagicMock()

    assert _run_unless_owner_inactive(actor, scheduler_id="sched-3", payload=payload) is None
    actor.assert_not_called()


def test_untracked_run_is_blocked_when_scheduler_setting_is_missing(log):
    actor = MagicMock()

    with patch(_SETTINGS, side_effect=KeyError("gone")):
        assert _run_unless_owner_inactive(actor, scheduler_id="sched-3", payload=_payload()) is None
    actor.assert_not_called()


def test_untracked_run_is_blocked_when_datasource_is_missing(scheduler_setting, log):
    actor = MagicMock()

    with patch(_DATASOURCE_CHECK, return_value=None):
        assert _run_unless_owner_inactive(actor, scheduler_id="sched-3", payload=_payload()) is None
    actor.assert_not_called()
