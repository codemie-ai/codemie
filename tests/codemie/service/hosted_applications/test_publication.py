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

# ruff: noqa: F811 - the reused fixtures are requested by parameter name
"""Marketplace publish, unpublish and activate under the platform access model.

Reuses the catalog suite's SQLite tables, personas and fake management transaction. The version-eligibility SQL
(a join over versions, jobs and storage attempts) is PostgreSQL territory, so a stand-in answers it here.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.repository import HostedApplicationRepository, as_uuid
from codemie.rest_api.models.hosted_application import HostedApplications as App
from codemie.rest_api.models.hosted_application_requests import AppCreate, AppPatch, PublishRequest, VersionSelection
from codemie.rest_api.routers.hosted_applications import router
from codemie.service.hosted_applications.catalog import HostedApplicationCatalog
from tests.codemie.service.hosted_applications.conftest import PROJECT
from tests.codemie.service.hosted_applications.test_catalog import (  # noqa: F401 - fixtures reused by name
    CATEGORY_A,
    CATEGORY_B,
    TEAM,
    catalog,
    create,
    engine,
    people,
    repository,
    set_app,
)

OLD, NEW = uuid4(), uuid4()

PUBLISH = PublishRequest(categories=[CATEGORY_A])


@pytest_asyncio.fixture
async def live(database):
    """A second, real-PostgreSQL catalog (``database`` comes from the directory ``conftest.py``).

    The ``catalog`` fixture above fakes the management transaction: its replay lookup is keyed by
    ``(action, target, key)`` alone and never compares the stored payload, so it cannot exercise the real
    idempotency-conflict check. That check lives in ``HostedTransaction.operation`` itself, so the one test that
    needs it runs against the real repository instead.
    """
    engine, _ = database

    @asynccontextmanager
    async def sessions():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    repo = HostedApplicationRepository(sessions, secret=b"s" * 48, quota_bytes=2 * 1024**3)
    return HostedApplicationCatalog(repo, directory=None)


@pytest.fixture(autouse=True)
def eligible(catalog, monkeypatch):
    """Every version but ``BLOCKED`` is accepted; the stand-in carries what activation copies to the app."""
    blocked = uuid4()
    catalog.blocked = blocked

    async def one(tx, app, version_id, *, activation=False):
        if version_id == blocked:
            return None
        return SimpleNamespace(id=version_id, artifact_kind="html")

    monkeypatch.setattr(catalog, "_eligible_version", one)


async def row(engine, app_id):
    async with AsyncSession(engine) as session:
        return await session.get(App, as_uuid(app_id))


def call(catalog, name, actor, app, *args):
    return getattr(catalog, name)(actor, app["id"], *args, app["etag"], str(uuid4()))


@pytest.mark.asyncio
async def test_publish_sets_is_global_and_categories_and_leaves_shared_and_the_access_revision(catalog, people, engine):
    app = await create(catalog, people["creator"])
    published = await call(catalog, "publish", people["creator"], app, PUBLISH)
    stored = await row(engine, app["id"])
    assert (stored.is_global, stored.shared, stored.access_revision, stored.categories) == (
        True,
        False,
        1,
        [CATEGORY_A],
    )
    assert published["revision"] == 2
    off = await call(catalog, "unpublish", people["creator"], published)
    stored = await row(engine, app["id"])
    assert (stored.is_global, stored.shared, stored.access_revision) == (False, False, 1)
    assert off["revision"] == 3


@pytest.mark.asyncio
async def test_publish_replaces_the_previous_categories_rather_than_merging_them(catalog, people, engine):
    app = await create(catalog, people["creator"])
    published = await call(catalog, "publish", people["creator"], app, PUBLISH)
    off = await call(catalog, "unpublish", people["creator"], published)
    republished = await call(catalog, "publish", people["creator"], off, PublishRequest(categories=[CATEGORY_B]))
    stored = await row(engine, app["id"])
    assert (stored.categories, republished["categories"]) == (
        [CATEGORY_B],
        [{"id": CATEGORY_B, "name": "Category B"}],
    )


@pytest.mark.asyncio
async def test_unpublish_keeps_the_categories(catalog, people, engine):
    app = await create(catalog, people["creator"])
    published = await call(catalog, "publish", people["creator"], app, PUBLISH)
    off = await call(catalog, "unpublish", people["creator"], published)
    stored = await row(engine, app["id"])
    assert (stored.is_global, stored.categories) == (False, [CATEGORY_A])
    assert off["categories"] == [{"id": CATEGORY_A, "name": "Category A"}]


@pytest.mark.asyncio
async def test_publish_with_an_unknown_category_id_is_refused_and_leaves_the_app_unchanged(catalog, people, engine):
    app = await create(catalog, people["creator"], categories=[CATEGORY_A])
    with pytest.raises(HostedApplicationError) as error:
        await call(catalog, "publish", people["creator"], app, PublishRequest(categories=["missing"]))
    assert (error.value.code, error.value.status) == ("INVALID_CATEGORIES", 422)
    stored = await row(engine, app["id"])
    assert (stored.is_global, stored.categories, stored.revision) == (False, [CATEGORY_A], 1)


@pytest.mark.asyncio
async def test_publishing_needs_no_active_version(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await call(catalog, "publish", people["creator"], app, PUBLISH)
    assert (await row(engine, app["id"])).active_version_id is None


@pytest.mark.asyncio
async def test_patching_shared_leaves_is_global_unchanged(catalog, people, engine):
    # A category is required so the published app's post-patch category check (task 4) does not reject this
    # metadata-only patch; publish itself requires one too (task 5), passed here through ``PUBLISH``.
    app = await create(catalog, people["creator"], categories=[CATEGORY_A])
    published = await call(catalog, "publish", people["creator"], app, PUBLISH)
    await catalog.patch_app(people["creator"], app["id"], AppPatch(shared=True), published["etag"], str(uuid4()))
    stored = await row(engine, app["id"])
    assert (stored.shared, stored.is_global) == (True, True)


@pytest.mark.asyncio
async def test_publish_replays_by_key_and_checks_the_revision(catalog, people):
    app = await create(catalog, people["creator"])
    key = str(uuid4())
    first = await catalog.publish(people["creator"], app["id"], PUBLISH, app["etag"], key)
    assert await catalog.publish(people["creator"], app["id"], PUBLISH, app["etag"], key) == first
    with pytest.raises(HostedApplicationError) as error:
        await catalog.unpublish(people["creator"], app["id"], app["etag"], str(uuid4()))
    assert error.value.code == "REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_publish_with_the_same_key_but_different_categories_or_their_order_conflicts(live, identity):
    owner = identity("owner")
    created = await live.create_app(owner, AppCreate(name="App", description="Text", project=PROJECT), str(uuid4()))
    key = str(uuid4())
    await live.publish(owner, created["id"], PublishRequest(categories=[CATEGORY_A, CATEGORY_B]), created["etag"], key)
    with pytest.raises(HostedApplicationError) as error:
        await live.publish(
            owner, created["id"], PublishRequest(categories=[CATEGORY_B, CATEGORY_A]), created["etag"], key
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(HostedApplicationError) as error:
        await live.publish(owner, created["id"], PublishRequest(categories=[CATEGORY_A]), created["etag"], key)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_publish_on_a_suspended_app_is_refused_but_unpublish_is_allowed(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], is_global=True, security_state="suspended")
    with pytest.raises(HostedApplicationError) as error:
        await call(catalog, "publish", people["creator"], app, PUBLISH)
    assert (error.value.code, error.value.status) == ("APP_SUSPENDED", 409)
    await call(catalog, "unpublish", people["creator"], app)
    assert (await row(engine, app["id"])).is_global is False


@pytest.mark.asyncio
@pytest.mark.parametrize("persona", ["member", "outsider"])
async def test_members_and_marketplace_viewers_cannot_publish_unpublish_or_activate(catalog, people, engine, persona):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True, is_global=True)
    for name, args in (
        ("publish", (PUBLISH,)),
        ("unpublish", ()),
        ("activate_version", (VersionSelection(version_id=NEW),)),
    ):
        with pytest.raises(HostedApplicationError) as error:
            await call(catalog, name, people[persona], app, *args)
        assert error.value.status == 404
    assert (await row(engine, app["id"])).active_version_id is None


@pytest.mark.asyncio
async def test_a_project_admin_publishes_for_the_creator(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await call(catalog, "publish", people["padmin"], app, PUBLISH)
    assert (await row(engine, app["id"])).is_global is True


@pytest.mark.asyncio
async def test_activate_on_an_unpublished_app_moves_the_active_and_selected_versions(catalog, people, engine):
    app = await create(catalog, people["creator"])
    changed = await call(catalog, "activate_version", people["creator"], app, VersionSelection(version_id=NEW))
    stored = await row(engine, app["id"])
    assert (stored.active_version_id, stored.selected_version_id, stored.artifact_kind) == (NEW, NEW, "html")
    assert (stored.is_global, stored.access_revision, changed["revision"]) == (False, 1, 2)


@pytest.mark.asyncio
async def test_select_version_moves_only_the_working_version(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await call(catalog, "select_version", people["creator"], app, VersionSelection(version_id=NEW))
    stored = await row(engine, app["id"])
    assert (stored.active_version_id, stored.selected_version_id) == (None, NEW)


@pytest.mark.asyncio
async def test_activate_refuses_a_version_that_is_not_accepted(catalog, people, engine):
    app = await create(catalog, people["creator"])
    with pytest.raises(HostedApplicationError) as error:
        await call(catalog, "activate_version", people["creator"], app, VersionSelection(version_id=catalog.blocked))
    assert error.value.code == "VERSION_NOT_ACCEPTED"
    assert (await row(engine, app["id"])).active_version_id is None


@pytest.mark.asyncio
async def test_activate_on_a_suspended_app_is_refused(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], security_state="suspended")
    with pytest.raises(HostedApplicationError) as error:
        await call(catalog, "activate_version", people["admin"], app, VersionSelection(version_id=NEW))
    assert (error.value.code, error.value.status) == ("APP_SUSPENDED", 409)
    assert (await row(engine, app["id"])).active_version_id is None


def test_the_removed_sharing_routes_are_gone_and_the_new_ones_exist():
    routes = {(method, route.path) for route in router.routes for method in route.methods}
    base = "/v1/hosted-applications"
    for gone in (
        ("GET", base + "/{app_id}/sharing"),
        ("PUT", base + "/{app_id}/sharing"),
        ("POST", base + "/{app_id}/unpublish"),
        ("GET", base + "/{app_id}/sharing/recipients"),
    ):
        assert gone not in routes
    for present in ("marketplace/publish", "marketplace/unpublish", "activate", "select-version"):
        assert ("POST", f"{base}/{{app_id}}/{present}") in routes
