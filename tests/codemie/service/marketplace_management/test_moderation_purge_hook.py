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

"""Deleting an assistant must take its moderation data with it (EXECUTION-PLAN 8.7).

Traces F-39, R12, S16.

`design-persistence.md` §2.4 deliberately gives `assistant_id` no foreign key to
`assistants.id`, so nothing in the database removes an assistant's verdicts,
findings, similarity rows, LLM assessments or events when the assistant goes.
The consequence is the bug the user reported: a deleted assistant reappears as a
Cleanup row with a NULL name and 404s when clicked.

The *statements* are pinned against a real PostgreSQL in
`tests/codemie/external/alembic/test_marketplace_migrations.py`. These tests pin
the two things that file cannot see:

  1. the delete path actually calls the purge, and
  2. a purge that fails does not take the delete down with it -- a moderation
     table that is unreachable must not make assistants undeletable.

and the wiring of the sweep that catches everything deleted before this shipped.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI


class _FakeAssistant:
    def __init__(self) -> None:
        self.id = "assistant-being-deleted"
        self.project = "demo"
        self.mcp_servers = []
        self.deleted = False

    def delete(self) -> None:
        self.deleted = True


@pytest.fixture
def stubbed_delete_path(monkeypatch: pytest.MonkeyPatch) -> _FakeAssistant:
    """Neutralise everything `delete_assistant` touches except the purge."""
    from codemie.rest_api.models.assistant import Assistant
    from codemie.service.assistant.assistant_share_service import AssistantShareService
    from codemie.service.guardrail.guardrail_service import GuardrailService
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    fake = _FakeAssistant()
    monkeypatch.setattr(Assistant, "find_by_id", classmethod(lambda cls, _id: fake))
    monkeypatch.setattr(AssistantShareService, "remove_all_grants", classmethod(lambda cls, *_a, **_kw: None))
    monkeypatch.setattr(
        SchedulerSettingsService,
        "delete_integrations_by_resource",
        classmethod(lambda cls, **_kw: 0),
    )
    monkeypatch.setattr(
        GuardrailService,
        "remove_guardrail_assignments_for_entity",
        classmethod(lambda cls, *_a, **_kw: None),
    )
    return fake


def _capture_purge(monkeypatch: pytest.MonkeyPatch, *, raises: bool = False) -> list[tuple]:
    from codemie.service.marketplace_management import audit_retention

    calls: list[tuple] = []

    class _Repo:
        def purge_entity(self, *, entity_kind, entity_id, session):  # noqa: ANN001
            calls.append((entity_kind, entity_id))
            if raises:
                raise RuntimeError("mm_audit_verdict is unreachable")
            return 7

        def purge_orphans(self, *, entity_kind, session, limit=1000):  # noqa: ANN001
            raise AssertionError("the delete path must purge one entity, not sweep")

    monkeypatch.setattr(audit_retention, "marketplace_moderation_purge_repository", _Repo())

    class _Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            pass

        def delete(self, obj):
            obj.delete()

    monkeypatch.setattr("sqlmodel.Session", _Session)
    # delete_assistant imported Session by name, so it needs the stub too
    monkeypatch.setattr("codemie.rest_api.models.assistant.Session", _Session)

    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: None))
    return calls


def test_delete_assistant_purges_its_moderation_data(
    stubbed_delete_path: _FakeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook fires, for this assistant, in the delete path."""
    from codemie.rest_api.models.assistant import Assistant

    calls = _capture_purge(monkeypatch)

    Assistant.delete_assistant(stubbed_delete_path.id)

    assert calls == [
        ("ASSISTANT", stubbed_delete_path.id)
    ], f"delete_assistant did not purge the moderation data: {calls}"
    assert stubbed_delete_path.deleted, "the assistant itself must still be deleted"


def test_a_failing_purge_does_not_block_the_delete(
    stubbed_delete_path: _FakeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreachable moderation table must not make assistants undeletable.

    The sweep exists precisely so that a delete which lost its purge is still
    reclaimed later, so failing closed here would trade a retention lag for an
    outage on a core endpoint.
    """
    calls = _capture_purge(monkeypatch, raises=True)

    from codemie.rest_api.models.assistant import Assistant

    Assistant.delete_assistant(stubbed_delete_path.id)

    assert calls, "the purge was never attempted"
    assert stubbed_delete_path.deleted, "a failed purge must not abort the delete"


# ---------------------------------------------------------------------------
# The sweep job
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    return FastAPI()


def _install_sweep(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_leader: bool,
    raises: bool = False,
) -> list[str]:
    from codemie.clients import postgres as pg
    from codemie.configs import config
    from codemie.rest_api import main
    from codemie.service.marketplace_management import audit_retention
    from codemie.utils import leader_lock as leader_lock_module

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: None))
    monkeypatch.setattr(config, "MARKETPLACE_MANAGEMENT_ENABLED", True)

    @asynccontextmanager
    async def _lock(_lock_id):
        yield is_leader

    monkeypatch.setattr(leader_lock_module, "async_leader_lock", _lock)

    swept: list[str] = []

    class _Repo:
        def purge_orphans(self, *, entity_kind, session, limit=1000):  # noqa: ANN001
            swept.append(entity_kind)
            if raises:
                raise RuntimeError("database went away")
            return 0

        def purge_entity(self, *, entity_kind, entity_id, session):  # noqa: ANN001
            raise AssertionError("the sweep must not purge a named entity")

    monkeypatch.setattr(audit_retention, "marketplace_moderation_purge_repository", _Repo())

    class _Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            pass

        def delete(self, obj):
            obj.delete()

    monkeypatch.setattr("sqlmodel.Session", _Session)
    # delete_assistant imported Session by name, so it needs the stub too
    monkeypatch.setattr("codemie.rest_api.models.assistant.Session", _Session)

    main._setup_marketplace_moderation_purge_scheduler(app)
    return swept


def _run_one_sweep(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, *, is_leader: bool, raises: bool = False
) -> list[str]:
    async def _scenario() -> list[str]:
        swept = _install_sweep(app, monkeypatch, is_leader=is_leader, raises=raises)
        scheduler = app.state.marketplace_moderation_purge_scheduler
        job = scheduler.get_job("marketplace_moderation_purge")
        assert job is not None, "the sweep was never scheduled"
        try:
            await job.func()
        finally:
            scheduler.shutdown(wait=False)
        return swept

    return asyncio.run(_scenario())


def test_the_sweep_runs_without_anyone_enabling_retention(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deletion cleanup is a policy obligation, not an operator preference.

    `MARKETPLACE_AUDIT_RETENTION_DAYS` defaults to 0 and gates the *age-based*
    purge of audit history, which is destructive and opt-in on purpose. Removing
    moderation data about entities that no longer exist is neither: it is what
    F-39 asks for, so it must not be hidden behind that switch.
    """
    from codemie.configs import config

    assert config.MARKETPLACE_AUDIT_RETENTION_DAYS == 0

    swept = _run_one_sweep(app, monkeypatch, is_leader=True)

    assert swept == ["ASSISTANT"], (
        "the sweep must run for the one entity kind whose ids are catalogued in " f"`assistants`; got {swept}"
    )


def test_a_non_leader_pod_sweeps_nothing(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pod schedules it; exactly one of them may run the DELETE."""
    assert _run_one_sweep(app, monkeypatch, is_leader=False) == []


def test_a_failing_sweep_does_not_take_the_scheduler_down(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad night must not stop every later sweep."""
    assert _run_one_sweep(app, monkeypatch, is_leader=True, raises=True) == ["ASSISTANT"]


def test_the_sweep_is_off_when_the_feature_is_off(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """The kill switch (F-16) has to reach this job too."""
    from codemie.configs import config
    from codemie.rest_api import main

    monkeypatch.setattr(config, "MARKETPLACE_MANAGEMENT_ENABLED", False)
    main._setup_marketplace_moderation_purge_scheduler(app)

    assert not hasattr(app.state, "marketplace_moderation_purge_scheduler")
