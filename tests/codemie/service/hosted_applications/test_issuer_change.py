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

"""AC4: changing JWT_ISSUER does not change the user, so the user keeps their hosted applications."""

from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.hosted_application_requests import VersionSelection
from codemie.rest_api.models.user_management import UserDB, UserProject
from tests.codemie.service.hosted_applications.conftest import PROJECT
from codemie.rest_api.security.user_providers.persistent import PersistentUserProvider
from codemie.service.jwt_service import JwtLocalService
from tests.codemie.service.hosted_applications.test_covers import usage
from tests.codemie.service.hosted_applications.test_worker import queued


def users_table(users):
    """Stand-in for the users table, keyed by users.id; everything above it runs for real."""

    @asynccontextmanager
    async def session():
        yield MagicMock(commit=AsyncMock(), rollback=AsyncMock())

    async def get_by_id(session, user_id):
        return users.get(user_id)

    service = "codemie.service.user.authentication_service"
    return (
        patch("codemie.clients.postgres.get_async_session", session),
        patch(f"{service}.user_repository.aget_by_id", AsyncMock(side_effect=get_by_id)),
        patch(f"{service}.user_repository.aupdate_last_login", AsyncMock()),
        patch(
            f"{service}.AuthenticationService._create_first_login_user",
            AsyncMock(side_effect=AssertionError("a sign-in created a second users row")),
        ),
        patch(f"{service}.user_project_repository.aget_by_user_id", AsyncMock(return_value=[])),
        patch(f"{service}.user_kb_repository.aget_by_user_id", AsyncMock(return_value=[])),
        patch(
            "codemie.service.project.personal_project_service.personal_project_service.ensure_personal_project_async",
            AsyncMock(),
        ),
        patch("codemie.core.hosted_applications.identity.record_platform_identity"),
    )


async def sign_in(provider, token, users):
    """One request through the persistent provider and the real persistent-user lookup."""
    request = MagicMock()
    request.cookies.get = MagicMock(return_value=None)
    request.headers.get = MagicMock(
        side_effect=lambda name, default=None: f"Bearer {token}" if name == "Authorization" else None
    )
    with ExitStack() as stack:
        for item in users_table(users):
            stack.enter_context(item)
        return await provider.authenticate_and_load_user(request, MagicMock())


@pytest.mark.asyncio
async def test_changed_issuer_keeps_the_user_and_everything_they_own(processing, identity, monkeypatch, tmp_path):
    service, repo, _, worker, _ = processing
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "IDP_PROVIDER", "local")
    monkeypatch.setattr(config, "JWT_PRIVATE_KEY_PATH", str(tmp_path / "jwt" / "private.pem"))
    monkeypatch.setattr(config, "JWT_PUBLIC_KEY_PATH", str(tmp_path / "jwt" / "public.pem"))
    monkeypatch.setattr("codemie.service.jwt_service.jwt_service", JwtLocalService())
    monkeypatch.setattr("codemie.rest_api.security.jwt_local.jwt_service", JwtLocalService())
    from codemie.rest_api.security import jwt_local

    idp_user_id, provider = str(uuid4()), PersistentUserProvider()
    row = UserDB(id=idp_user_id, username="idp", email="idp@example.test", name="IDP User", auth_source="local")
    users = {idp_user_id: row}

    monkeypatch.setattr(config, "JWT_ISSUER", "issuer-before")
    old_token = jwt_local.generate_access_token(idp_user_id, "idp@example.test")
    first = await sign_in(provider, old_token, users)

    owner = identity(first.id)
    # The access rules read the platform's users table, which this test's PostgreSQL schema holds.
    async with repo.sessions() as session, session.begin():
        await session.execute(
            UserDB.__table__.insert().values(
                id=first.id,
                username="idp",
                email="idp@example.test",
                user_type="regular",
                is_active=True,
                is_admin=False,
                is_maintainer=False,
                is_auditor=False,
                auth_source="local",
                email_verified=False,
            )
        )
        # Project membership is read from ``user_projects`` too, not from the request user.
        await session.execute(
            UserProject.__table__.insert().values(
                id=uuid4().hex, user_id=first.id, project_name=PROJECT, is_project_admin=False, is_default=False
            )
        )
    app, staged = await queued(service, owner)
    assert await worker.run_one()
    current = await service.get_app(owner, app["id"])
    activated = await service.activate_version(
        owner, app["id"], VersionSelection(version_id=UUID(staged["version_id"])), current["etag"], str(uuid4())
    )
    published = await service.publish(owner, app["id"], activated["etag"], str(uuid4()))
    before = {
        "versions": (await service.list_versions(owner, app["id"]))["items"],
        "access": (published["project"], published["shared"], published["is_global"]),
        "usage": await usage(service, repo, owner),
    }
    assert before["usage"]["stored_bytes"] > 0 and before["access"][2]

    monkeypatch.setattr(config, "JWT_ISSUER", "issuer-after")
    with pytest.raises(ExtendedHTTPException) as rejected:
        await sign_in(provider, old_token, users)
    assert rejected.value.code == 401
    again = await sign_in(provider, jwt_local.generate_access_token(idp_user_id, "idp@example.test"), users)

    assert again.id == first.id == idp_user_id
    assert users == {idp_user_id: row} and row.id == idp_user_id
    same = identity(again.id)
    assert [item["id"] for item in (await service.list_apps(same))["items"]] == [app["id"]]
    assert "write" in (await service.get_app(same, app["id"]))["user_abilities"]
    assert (await service.list_versions(same, app["id"]))["items"] == before["versions"]
    again_app = await service.get_app(same, app["id"])
    assert (again_app["project"], again_app["shared"], again_app["is_global"]) == before["access"]
    assert await usage(service, repo, same) == before["usage"]
