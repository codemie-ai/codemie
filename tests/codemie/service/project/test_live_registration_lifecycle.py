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

"""A registration racing a project rename or delete, against a disposable Postgres (EPMCDME-16440, IR-004).

The lifecycle transaction and the registration take the project row lock (`lock_project`), so whichever comes second
waits for the first to commit and then sees its result. Without TEST_LOCAL_PG_URL every test is skipped.
"""

import os
import threading
import time

import pytest
from sqlalchemy import create_engine, delete, text, update
from sqlmodel import Session, select

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application, CostCenter
from codemie.rest_api.models.repository_project_registration import RepositoryProjectRegistration as Row
from codemie.service.project.repository_registration_service import RepositoryRegistrationRepository as Regs
from tests.codemie.service.project import registration_db

_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

REMOTE = "https://host.example/Org/Repo"
KEY = "host.example/Org/Repo"
OTHER = "https://host.example/Org/Other"
OTHER_KEY = "host.example/Org/Other"
_TABLES = [CostCenter.__table__, *registration_db.TABLES]


@pytest.fixture
def engine():
    engine = create_engine(_URL.replace("postgresql://", "postgresql+psycopg2://", 1), pool_size=5)
    saved = [(t, t.schema) for t in registration_db.TABLES]
    for t in registration_db.TABLES:
        t.schema = None
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DROP TABLE IF EXISTS repository_project_registrations, applications, cost_centers CASCADE")
            )
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))  # the project name index uses it
        Row.metadata.create_all(engine, tables=_TABLES)
        with Session(engine) as s:
            registration_db.add_project(s, "old")
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DROP TABLE IF EXISTS repository_project_registrations, applications, cost_centers CASCADE")
            )
        registration_db.restore(saved)
        engine.dispose()


def _owners(engine) -> dict[str, str]:
    with Session(engine) as s:
        return {r.normalized_remote: r.project_name for r in s.exec(select(Row)).all()}


def _in_thread(fn):
    """Run `fn` in a thread; (thread, outcome) where outcome holds the result or the exception."""
    outcome: dict = {}

    def run():
        try:
            outcome["result"] = fn()
        except Exception as exc:  # noqa: BLE001 - handed back to the test
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def _blocked(thread) -> bool:
    time.sleep(0.8)
    return thread.is_alive()


def _lifecycle(session, new_name: str | None) -> None:
    """What a rename (`new_name`) or a delete does to the registrations and the project, uncommitted."""
    assert Regs.lock_project(session, "old") is not None
    if new_name:
        Regs.rename_project(session, "old", new_name)
        session.execute(update(Application).where(Application.name == "old").values(name=new_name, id=new_name))
    else:
        Regs.delete_for_project(session, "old")
        session.execute(delete(Application).where(Application.name == "old"))


def _register(engine, remote=REMOTE):
    def go():
        with Session(engine) as s:
            return Regs.register(s, "old", "u", remote=remote).normalized_remote

    return go


@pytest.mark.parametrize("new_name, kept", [(None, {}), ("renamed", {OTHER_KEY: "renamed"})])
def test_a_put_authorized_before_the_lifecycle_commits_writes_nothing_after_it(engine, new_name, kept):
    with Session(engine) as seed:
        Regs.register(seed, "old", "u", remote=OTHER)
    lifecycle = Session(engine)
    _lifecycle(lifecycle, new_name)  # holds the project row lock, uncommitted
    thread, outcome = _in_thread(_register(engine))

    assert _blocked(thread), "the registration must wait for the lifecycle transaction"
    lifecycle.commit()
    thread.join(10)

    assert isinstance(outcome.get("error"), ExtendedHTTPException) and outcome["error"].code == 404
    assert _owners(engine) == kept  # no mapping to the old name, rename carried the earlier one along
    lifecycle.close()


@pytest.mark.parametrize("new_name, kept", [(None, {}), ("renamed", {KEY: "renamed"})])
def test_a_registration_committing_first_is_moved_or_dropped_by_the_lifecycle(engine, new_name, kept):
    registering = Session(engine)
    assert Regs.lock_project(registering, "old") is not None
    registering.add(Row(normalized_remote=KEY, project_name="old", created_by="u"))
    registering.flush()  # inserted, not committed, project row lock held

    def lifecycle():
        with Session(engine) as s:
            _lifecycle(s, new_name)
            s.commit()

    thread, outcome = _in_thread(lifecycle)
    assert _blocked(thread), "the lifecycle must wait for the registration transaction"
    registering.commit()
    thread.join(10)

    assert "error" not in outcome
    assert _owners(engine) == kept
    registering.close()


def test_a_rolled_back_lifecycle_lets_the_waiting_registration_succeed(engine):
    lifecycle = Session(engine)
    _lifecycle(lifecycle, "renamed")
    thread, outcome = _in_thread(_register(engine))
    assert _blocked(thread)
    lifecycle.rollback()
    thread.join(10)

    assert outcome.get("result") == KEY
    assert _owners(engine) == {KEY: "old"}
    lifecycle.close()
