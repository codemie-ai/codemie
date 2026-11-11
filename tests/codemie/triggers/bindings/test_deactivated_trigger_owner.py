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

"""A deactivated owner's schedulers and webhooks must not run (EPMCDME-13928)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.triggers.bindings import utils
from codemie.triggers.bindings.cron import _run_unless_owner_inactive, _tracked_run, _tracked_run_sync
from codemie.triggers.bindings.webhook import WebhookService

_REPO = "codemie.repository.user_repository.user_repository"


@pytest.fixture(autouse=True)
def _user_management_on():
    with patch("codemie.triggers.bindings.utils.config.ENABLE_USER_MANAGEMENT", True):
        yield


@pytest.fixture
def user_lookup():
    """Patch the owner lookups: active_user is what get_active_by_id returns, any_user what get_by_id returns."""
    state = {"active_user": None, "any_user": None}
    with (
        patch("codemie.clients.postgres.get_session") as mock_get_session,
        patch(f"{_REPO}.get_active_by_id", side_effect=lambda _s, _uid: state["active_user"]),
        patch(f"{_REPO}.get_by_id", side_effect=lambda _s, _uid: state["any_user"]),
    ):
        mock_get_session.return_value.__enter__.return_value = MagicMock()
        yield state


def _db_user(user_id="owner-1"):
    user = MagicMock()
    user.id = user_id
    user.username = "owner"
    user.name = "Owner"
    user.email = "owner@example.com"
    return user


def test_resolve_trigger_user_returns_active_owner(user_lookup):
    user_lookup["active_user"] = user_lookup["any_user"] = _db_user()

    user = utils.resolve_trigger_user("owner-1")

    assert user.id == "owner-1"
    assert user.email == "owner@example.com"


def test_resolve_trigger_user_returns_none_for_deactivated_owner(user_lookup):
    user_lookup["any_user"] = _db_user()

    with patch("codemie.triggers.bindings.utils.config.ENV", "dev"):
        assert utils.resolve_trigger_user("owner-1") is None


def test_resolve_trigger_user_returns_none_for_unknown_owner_outside_local(user_lookup):
    with patch("codemie.triggers.bindings.utils.config.ENV", "dev"):
        assert utils.resolve_trigger_user("legacy-id") is None


def test_resolve_trigger_user_returns_none_when_lookup_fails_outside_local():
    with (
        patch("codemie.triggers.bindings.utils.config.ENV", "dev"),
        patch("codemie.clients.postgres.get_session", side_effect=RuntimeError("db down")),
    ):
        assert utils.resolve_trigger_user("owner-1") is None


def test_resolve_trigger_user_keeps_stand_in_for_unknown_owner_on_local(user_lookup):
    with patch("codemie.triggers.bindings.utils.config.ENV", "local"):
        assert utils.resolve_trigger_user("local-id").id == "local-id"


def test_resolve_trigger_user_keeps_stand_in_with_user_management_off(user_lookup):
    """That mode keeps no user rows, so automations must keep running for every owner."""
    with (
        patch("codemie.triggers.bindings.utils.config.ENV", "dev"),
        patch("codemie.triggers.bindings.utils.config.ENABLE_USER_MANAGEMENT", False),
    ):
        assert utils.resolve_trigger_user("jwt-user").id == "jwt-user"
        assert utils.is_trigger_owner_active("jwt-user") is True


def test_resolve_trigger_user_refuses_deactivated_owner_on_local(user_lookup):
    user_lookup["any_user"] = _db_user()

    with patch("codemie.triggers.bindings.utils.config.ENV", "local"):
        assert utils.resolve_trigger_user("owner-1") is None


@pytest.mark.parametrize(
    ("active", "existing", "expected"),
    [(True, True, True), (False, True, False), (False, False, False)],
    ids=["active", "deactivated", "unknown"],
)
def test_is_trigger_owner_active_outside_local(user_lookup, active, existing, expected):
    user_lookup["active_user"] = _db_user() if active else None
    user_lookup["any_user"] = _db_user() if existing else None

    with patch("codemie.triggers.bindings.utils.config.ENV", "dev"):
        assert utils.is_trigger_owner_active("owner-1") is expected


@pytest.fixture
def run_repo():
    with (
        patch("codemie.repository.scheduler_run_repository.SchedulerRunRepository") as mock_repo_cls,
        patch("codemie.rest_api.models.scheduler_run.SchedulerRun"),
    ):
        repo = mock_repo_cls.return_value
        repo.create.return_value = MagicMock(id="run-1")
        yield repo


def _assert_skipped(repo):
    repo.update.assert_called_once()
    fields = repo.update.call_args[0][1]
    assert fields["status"] == "skipped"
    assert fields["result_data"] == {"content": "The owner of this scheduler is not active"}


@pytest.mark.asyncio
async def test_scheduled_assistant_run_is_skipped_for_deactivated_owner(run_repo):
    actor = AsyncMock()

    with patch("codemie.triggers.bindings.cron.is_trigger_owner_active", return_value=False) as check:
        result = await _tracked_run(actor, scheduler_id="sched-1", assistant_id="a-1", user_id="owner-1")

    assert result is None
    check.assert_called_once_with("owner-1")
    actor.assert_not_called()
    _assert_skipped(run_repo)


@pytest.mark.asyncio
async def test_scheduled_assistant_run_goes_ahead_for_active_owner(run_repo):
    actor = AsyncMock(return_value=None)

    with patch("codemie.triggers.bindings.cron.is_trigger_owner_active", return_value=True):
        await _tracked_run(actor, scheduler_id="sched-1", assistant_id="a-1", user_id="owner-1")

    actor.assert_awaited_once()
    assert run_repo.update.call_args[0][1]["status"] == "completed"


def test_scheduled_datasource_run_is_skipped_for_deactivated_owner(run_repo):
    actor = MagicMock()
    payload = MagicMock()
    payload.user.id = "owner-1"

    with patch("codemie.triggers.bindings.cron.is_trigger_owner_active", return_value=False) as check:
        _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="res-1", payload=payload)

    check.assert_called_once_with("owner-1")
    actor.assert_not_called()
    _assert_skipped(run_repo)


def test_scheduled_datasource_run_goes_ahead_for_active_owner(run_repo):
    actor = MagicMock(return_value=None)
    payload = MagicMock()
    payload.user.id = "owner-1"

    with (
        patch("codemie.triggers.bindings.cron.is_trigger_owner_active", return_value=True),
        patch("codemie.triggers.bindings.cron._extract_datasource_metrics", return_value=None),
    ):
        _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="res-1", payload=payload)

    actor.assert_called_once()
    assert run_repo.update.call_args[0][1]["status"] == "completed"


def test_datasource_webhook_skips_owner_deactivated_after_the_entry_check():
    datasource = MagicMock()
    datasource.project_name = "test_project"
    setting = MagicMock()
    setting.project_name = "test_project"
    setting.user_id = "owner-1"
    setting.alias = "hook-1"
    background_tasks = MagicMock()

    with (
        patch("codemie.triggers.bindings.webhook.validate_datasource", return_value=datasource),
        patch("codemie.triggers.bindings.webhook.resolve_trigger_user", return_value=None),
    ):
        WebhookService.handle_datasource(resource_id="ds-1", background_tasks=background_tasks, setting=setting)

    background_tasks.add_task.assert_not_called()


@pytest.mark.parametrize("resource_type", ["assistant", "workflow", "datasource"])
def test_webhook_of_deactivated_owner_is_acked_without_dispatch(resource_type):
    from codemie.service.settings.settings import SettingsService

    setting = MagicMock()
    setting.credential.side_effect = lambda key: {
        "webhook_id": "hook-id",
        WebhookService.SECURE_HEADER_NAME: None,
        WebhookService.SECURE_HEADER_VALUE: None,
        WebhookService.GITHUB_WEBHOOK_SECRET: None,
        WebhookService.RESOURCE_TYPE: resource_type,
        WebhookService.RESOURCE_ID: "res-1",
        WebhookService.IS_ENABLED: True,
    }.get(key)
    setting.project_name = "test_project"
    setting.user_id = "owner-1"
    setting.alias = "hook-1"
    background_tasks = MagicMock()

    with (
        patch.object(SettingsService, "retrieve_setting", return_value=setting),
        patch.object(WebhookService, "verify_security_header", return_value=True),
        patch("codemie.triggers.bindings.webhook.is_trigger_owner_active", return_value=False) as check,
        patch("codemie.triggers.bindings.webhook.WebhookMonitoringService") as monitoring,
        patch.object(WebhookService, "handle_assistant") as handle_assistant,
        patch.object(WebhookService, "handle_workflow") as handle_workflow,
        patch.object(WebhookService, "handle_datasource") as handle_datasource,
    ):
        response = WebhookService.invoke_webhook_logic(MagicMock(), "hook-id", background_tasks, b"{}")

    # 200, so a provider such as GitLab does not disable the hook while the owner is deactivated
    assert response.message == "Webhook received, not run: the owner of webhook 'hook-1' is not active."
    check.assert_called_once_with("owner-1")
    handle_assistant.assert_not_called()
    handle_workflow.assert_not_called()
    handle_datasource.assert_not_called()
    metric = monitoring.send_webhook_invocation_metric.call_args.kwargs
    assert metric["success"] is False
    assert metric["additional_attributes"] == {"error_cause": "owner_not_active"}


@pytest.mark.parametrize("runs", [False, True], ids=["inactive", "active"])
def test_scheduled_git_faq_run_checks_its_owner(runs):
    actor = MagicMock()
    payload = MagicMock()
    payload.user.id = "owner-1"

    with patch("codemie.triggers.bindings.cron.is_trigger_owner_active", return_value=runs) as check:
        _run_unless_owner_inactive(actor, scheduler_id="sched-3", payload=payload)

    check.assert_called_once_with("owner-1")
    if runs:
        actor.assert_called_once_with(payload=payload)
    else:
        actor.assert_not_called()


def test_datasource_scheduler_of_inactive_owner_is_not_scheduled():
    from codemie.triggers.bindings.cron import Cron

    binder = Cron.__new__(Cron)  # bypass __init__ (starts APScheduler)
    binder.scheduler = MagicMock()

    with patch("codemie.triggers.bindings.cron.resolve_trigger_user", return_value=None):
        result = binder._Cron__schedule_datasource_job(
            "code", MagicMock(), "job-id", "ds-id", "owner-1", "proj", "repo", ""
        )

    assert result is None
    binder.scheduler.add_job.assert_not_called()
