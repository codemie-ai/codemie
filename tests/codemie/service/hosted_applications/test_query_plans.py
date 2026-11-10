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

"""Hot list filters must probe their leading indexes instead of scanning every row."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from tests.codemie.service.hosted_applications.test_worker import queued


def recording(repo):
    statements = []
    original = repo.sessions

    class Recorder:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, key):
            return getattr(self.inner, key)

        async def execute(self, statement, *args, **kwargs):
            statements.append(statement)
            return await self.inner.execute(statement, *args, **kwargs)

    @asynccontextmanager
    async def sessions():
        async with original() as session:
            yield Recorder(session)

    repo.sessions = sessions
    return statements, original


async def plan(sessions, statement):
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    async with sessions() as session, session.begin():
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        return [row[0] for row in (await session.execute(text("EXPLAIN " + sql))).all()]


def index_condition(lines, index):
    for position, line in enumerate(lines):
        if f"using {index} on" in line:
            for detail in lines[position + 1 : position + 4]:
                if "Index Cond:" in detail:
                    return detail
    return None


@pytest.mark.asyncio
async def test_latest_version_state_filter_probes_the_version_index(processing, identity):
    service, repo, _, _, _ = processing
    context = identity()
    app, _ = await queued(service, context)
    statements, original = recording(repo)
    try:
        page = await service.list_apps(context, version_state="queued")
    finally:
        repo.sessions = original
    assert [item["id"] for item in page["items"]] == [app["id"]]
    listing = next(
        statement
        for statement in statements
        if "validation_state" in str(statement) and str(statement).startswith("SELECT hosted_applications.id")
    )
    lines = await plan(original, listing)
    condition = index_condition(lines, "hosted_application_versions_app_id_sequence_key")
    assert condition and "app_id = hosted_applications.id" in condition, "\n".join(lines)


@pytest.mark.asyncio
async def test_gateway_authorize_spends_at_most_two_queries_per_content_request(launch, identity, database):
    """AC20: the access rule rides inside ``_load``; with the version provenance check that is two statements."""
    from uuid import UUID

    from sqlalchemy import event

    handoff, service, worker, _ = launch
    context = identity()
    app, queued_version = await queued(service, context)
    await worker.run_one()
    version_id = UUID(queued_version["version_id"])
    ticket = await handoff.issue(context, app["id"], purpose="owner_preview")
    cookie, _ = await handoff.consume(ticket["ticket"], handoff.host(version_id))
    engine = database[0]
    executed = []

    def record(connection, cursor, statement, *args):
        executed.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        allowed = await handoff.authorize(cookie, version_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert allowed["version"]["id"] == version_id
    queries = [statement for statement in executed if statement.lstrip().upper().startswith("SELECT")]
    assert len(queries) <= 2, queries
    assert "user_projects" in queries[0] and "users" in queries[0], "the access rule is part of the single load"
