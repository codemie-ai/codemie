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

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import literal_column, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BindParameter

from codemie.rest_api.models.category import Category
from codemie.rest_api.models.hosted_application import HostedApplications as App
from codemie.rest_api.models.hosted_application_requests import AppCreate
from codemie.repository.hosted_applications.repository import as_uuid
from codemie.service.hosted_applications.catalog import MAX_CATEGORY_FILTERS, list_filters
from tests.codemie.service.hosted_applications.conftest import PROJECT
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


def _inline_jsonb(element):
    """JSONB has no literal renderer, so the category filter's bound arrays become the constants EXPLAIN takes."""
    if isinstance(element, BindParameter) and isinstance(element.type, JSONB):
        return literal_column("'" + json.dumps(element.value) + "'::jsonb")
    return None


async def plan(sessions, statement):
    statement = visitors.replacement_traverse(statement, {}, _inline_jsonb)
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


async def seed_categories(engine, count):
    """``count`` further shared-platform categories, so a full-width category filter is a valid one."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ids = [f"plan-category-{index:02d}" for index in range(count)]
    async with engine.begin() as connection:
        for category_id in ids:
            await connection.execute(
                Category.__table__.insert().values(id=category_id, name=category_id, date=now, update_date=now)
            )
    return ids


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["marketplace", "project"])
async def test_a_full_width_category_filter_probes_the_categories_index(processing, identity, database, scope):
    """Every id is one ``@>`` containment, so a 50-id selection rides the GIN index, not a table scan."""
    service, repo, _, _, _ = processing
    engine = database[0]
    context = identity()
    ids = await seed_categories(engine, MAX_CATEGORY_FILTERS)
    app = await service.create_app(
        context,
        AppCreate(name="Planned", description="Text", project=PROJECT, categories=[ids[0]]),
        str(uuid4()),
    )
    async with engine.begin() as connection:
        await connection.execute(App.__table__.update().where(App.id == as_uuid(app["id"])).values(is_global=True))
    statements, original = recording(repo)
    try:
        page = await service.list_apps(context, scope=scope, category=ids)
    finally:
        repo.sessions = original
    assert [item["id"] for item in page["items"]] == [app["id"]]
    listing = next(
        statement
        for statement in statements
        if "@>" in str(statement) and str(statement).startswith("SELECT hosted_applications.id")
    )
    # One containment per id, nothing folded into a scan of the whole array.
    assert str(listing).count("@>") == MAX_CATEGORY_FILTERS
    # The scope's own leading index may still be the cheaper entry point (here ``hosted_applications_project``
    # on a near-empty table), so the category terms are planned on their own: every one of them has to be
    # answerable from the GIN index, in this scope as in any other.
    lines = await plan(original, select(App.id).where(*list_filters(App.__table__, categories=ids)))
    joined = "\n".join(lines)
    assert "ix_hosted_applications_categories" in joined, joined
    assert "Seq Scan on hosted_applications" not in joined, joined


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
