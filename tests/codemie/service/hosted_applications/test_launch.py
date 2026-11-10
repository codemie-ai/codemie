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

from uuid import UUID

import pytest

from codemie.core.hosted_applications.errors import HostedApplicationError
from tests.codemie.service.hosted_applications.test_worker import queued


@pytest.mark.asyncio
async def test_ticket_is_one_use_bound_to_version_host_and_normal_login(launch, identity):
    handoff, service, worker, _ = launch
    context = identity()
    app, queued_version = await queued(service, context)
    await worker.run_one()
    ticket = await handoff.issue(context, app["id"], purpose="owner_preview")
    host = "v-" + UUID(queued_version["version_id"]).hex + ".apps.localhost:8081"
    assert ticket["action"] == "http://" + host + "/_platform/launch"
    with pytest.raises(HostedApplicationError):
        await handoff.consume(ticket["ticket"], "v-" + "0" * 32 + ".apps.localhost:8081")
    cookie, target = await handoff.consume(ticket["ticket"], host)
    assert target == "/" and len(cookie) == 43
    with pytest.raises(HostedApplicationError):
        await handoff.consume(ticket["ticket"], host)
    allowed = await handoff.authorize(cookie, UUID(queued_version["version_id"]))
    assert allowed["version"]["id"] == UUID(queued_version["version_id"])
    with pytest.raises(HostedApplicationError):
        await handoff.authorize(ticket["ticket"], UUID(queued_version["version_id"]))


@pytest.mark.asyncio
async def test_delete_denies_existing_cookie(launch, identity):
    handoff, service, worker, _ = launch
    context = identity()
    app, queued_version = await queued(service, context)
    await worker.run_one()
    ticket = await handoff.issue(context, app["id"], purpose="owner_preview")
    host = "v-" + UUID(queued_version["version_id"]).hex + ".apps.localhost:8081"
    cookie, _ = await handoff.consume(ticket["ticket"], host)
    assert await handoff.authorize(cookie, UUID(queued_version["version_id"]))
    latest = await service.get_app(context, app["id"])
    await service.delete(context, app["id"], latest["etag"], str(__import__("uuid").uuid4()))
    with pytest.raises(HostedApplicationError):
        await handoff.authorize(cookie, UUID(queued_version["version_id"]))


@pytest.mark.asyncio
async def test_logout_revokes_ticket_cookie_and_future_second_old_credentials(launch, identity):
    handoff, service, worker, _ = launch
    context = identity()
    app, queued_version = await queued(service, context)
    await worker.run_one()
    ticket = await handoff.issue(context, app["id"], purpose="owner_preview")
    host = "v-" + UUID(queued_version["version_id"]).hex + ".apps.localhost:8081"
    cookie, _ = await handoff.consume(ticket["ticket"], host)
    await handoff.logout(context.identity)
    for credential in (cookie, ticket["ticket"]):
        with pytest.raises(HostedApplicationError):
            await handoff.authorize(credential, UUID(queued_version["version_id"]))
    with pytest.raises(HostedApplicationError):
        await handoff.issue(context, app["id"], purpose="owner_preview")


async def preview_cookie(handoff, service, worker, context):
    app, queued_version = await queued(service, context)
    await worker.run_one()
    ticket = await handoff.issue(context, app["id"], purpose="owner_preview")
    host = "v-" + UUID(queued_version["version_id"]).hex + ".apps.localhost:8081"
    cookie, _ = await handoff.consume(ticket["ticket"], host)
    return cookie, UUID(queued_version["version_id"])


@pytest.mark.asyncio
async def test_view_session_is_bound_to_the_principal_that_launched_it(launch, identity):
    handoff, service, worker, directory = launch
    cookie, version_id = await preview_cookie(handoff, service, worker, identity())
    await handoff.logout(identity("viewer").identity)
    allowed = await handoff.authorize(cookie, version_id)
    assert allowed["viewer_subject"] == "owner"
    await handoff.logout(identity().identity)
    with pytest.raises(HostedApplicationError):
        await handoff.authorize(cookie, version_id)


@pytest.mark.asyncio
async def test_view_session_is_bound_to_the_sign_in_session_id(launch, identity):
    handoff, service, worker, _ = launch
    cookie, version_id = await preview_cookie(handoff, service, worker, identity(sid="sid-a"))
    await handoff.logout(identity(sid="sid-b").identity)
    assert (await handoff.authorize(cookie, version_id))["viewer_subject"] == "owner"
    await handoff.logout(identity(sid="sid-a").identity)
    with pytest.raises(HostedApplicationError):
        await handoff.authorize(cookie, version_id)


@pytest.mark.asyncio
async def test_a_sign_in_with_another_sid_cannot_reuse_a_revoked_sign_in(launch, identity):
    handoff, service, worker, _ = launch
    first = identity(sid="sid-a")
    app, _ = await queued(service, first)
    await worker.run_one()
    await handoff.logout(first.identity)
    with pytest.raises(HostedApplicationError):
        await handoff.issue(first, app["id"], purpose="owner_preview")
    assert (await handoff.issue(identity(sid="sid-b"), app["id"], purpose="owner_preview"))["ticket"]


@pytest.mark.asyncio
async def test_viewer_launch_never_waits_for_an_owner_transaction_on_the_app_row(launch, identity):
    import asyncio

    from sqlalchemy import select

    from codemie.rest_api.models.hosted_application import HostedApplications as App

    handoff, service, worker, _ = launch
    app, _ = await queued(service, identity())
    await worker.run_one()
    # An edit that keeps the row's key (FOR NO KEY UPDATE) must not delay launches: the launch takes no
    # exclusive row lock, and its audit/ticket foreign-key checks only take FOR KEY SHARE.
    async with service.repository.sessions() as owner_edit, owner_edit.begin():
        await owner_edit.execute(select(App).where(App.id == UUID(app["id"])).with_for_update(key_share=True))
        tickets = await asyncio.wait_for(
            asyncio.gather(*(handoff.issue(identity(), app["id"], purpose="owner_preview") for _ in range(3))), 5
        )
    assert all(len(ticket["ticket"]) == 43 for ticket in tickets)


@pytest.mark.asyncio
async def test_ticket_issued_before_a_delete_cannot_be_consumed(launch, identity):
    from uuid import uuid4

    handoff, service, worker, _ = launch
    app, queued_version = await queued(service, identity())
    await worker.run_one()
    version = UUID(queued_version["version_id"])
    ticket = await handoff.issue(identity(), app["id"], purpose="owner_preview")
    # The ticket carries the access revision it was issued under; only delete and purge bump it.
    current = await service.get_app(identity(), app["id"])
    await service.delete(identity(), app["id"], current["etag"], str(uuid4()))
    with pytest.raises(HostedApplicationError):
        await handoff.consume(ticket["ticket"], handoff.host(version))


def days_ago(days):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(days=days)


async def launch_and_view(handoff, context, app, queued_version):
    ticket = await handoff.issue(context, app["id"], purpose="owner_preview")
    cookie, _ = await handoff.consume(ticket["ticket"], handoff.host(UUID(queued_version["version_id"])))
    return await handoff.authorize(cookie, UUID(queued_version["version_id"]))


@pytest.mark.asyncio
async def test_sign_in_with_unchecked_expiry_launches_long_after_login(launch, identity):
    handoff, service, worker, _ = launch
    context = identity(auth_time=days_ago(3), expiry_verified=False)
    app, queued_version = await queued(service, context)
    await worker.run_one()
    allowed = await launch_and_view(handoff, context, app, queued_version)
    assert allowed["version"]["id"] == UUID(queued_version["version_id"])


@pytest.mark.asyncio
async def test_sign_in_with_checked_expiry_keeps_the_limit_since_login(launch, identity):
    handoff, service, worker, _ = launch
    context = identity(auth_time=days_ago(3))
    app, queued_version = await queued(service, context)
    await worker.run_one()
    with pytest.raises(HostedApplicationError):
        await handoff.issue(context, app["id"], purpose="owner_preview")


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_verified", [False, True], ids=["unchecked", "checked"])
async def test_only_unchecked_expiry_renews_an_ended_launch_context(launch, identity, expiry_verified):
    from sqlalchemy import update

    from codemie.rest_api.models.hosted_application import HostedApplicationViewSessions as Credential

    handoff, service, worker, _ = launch
    context = identity(expiry_verified=expiry_verified)
    app, queued_version = await queued(service, context)
    await worker.run_one()
    await launch_and_view(handoff, context, app, queued_version)
    ended = days_ago(1)
    async with service.repository.sessions() as session, session.begin():
        await session.execute(
            update(Credential)
            .where(Credential.kind == "auth_context")
            .values(created_at=days_ago(2), binding_valid_until=ended, expires_at=ended)
        )
    if expiry_verified:
        with pytest.raises(HostedApplicationError):
            await handoff.issue(context, app["id"], purpose="owner_preview")
    else:
        allowed = await launch_and_view(handoff, context, app, queued_version)
        assert allowed["version"]["id"] == UUID(queued_version["version_id"])
