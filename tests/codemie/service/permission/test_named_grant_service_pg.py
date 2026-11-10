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

"""Database-backed checks of the named-grant statements; need an isolated PostgreSQL database."""

import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("HOSTED_TEST_DATABASE_URL"),
    reason="Set HOSTED_TEST_DATABASE_URL to an isolated PostgreSQL database",
)


@pytest.fixture
def pg_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(os.environ["HOSTED_TEST_DATABASE_URL"])
    with Session(engine) as session:
        yield session
        session.rollback()


def _seed_users(session, count):
    from codemie.rest_api.models.user_management import UserDB

    users = [
        UserDB(id=f"nga-u{i}", username=f"nga-u{i}", email=f"nga-u{i}@x.test", is_active=True, user_type="regular")
        for i in range(count)
    ]
    session.add_all(users)
    session.flush()
    return [u.id for u in users]


def test_mixed_batch_reports_inserted_and_returned_rows(pg_session):
    from uuid import uuid4

    from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
    from codemie.service.permission import named_grant_service as svc

    ids = _seed_users(pg_session, 3)
    rid = uuid4()
    link_id = uuid4()
    pg_session.add(
        Permission(
            resource_type=ResourceType.HOSTED_APPLICATION,
            resource_id=rid,
            principal_type=PrincipalType.USER,
            principal_id=ids[1],
            permission_level=svc.Action.READ,
            grant_source="link",
            source_link_id=link_id,
        )
    )
    pg_session.add(
        Permission(
            resource_type=ResourceType.HOSTED_APPLICATION,
            resource_id=rid,
            principal_type=PrincipalType.USER,
            principal_id=ids[2],
            permission_level=svc.Action.READ,
            grant_source="name",
        )
    )
    pg_session.flush()
    rows = pg_session.execute(svc.upsert_statement(ResourceType.HOSTED_APPLICATION, rid, None, ids)).all()
    by_principal = {r.principal_id: r.inserted for r in rows}
    assert by_principal == {ids[0]: True, ids[1]: False}


def _link_row(pg_session, rid, uid, link_id):
    from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
    from codemie.service.permission import named_grant_service as svc

    pg_session.add(
        Permission(
            resource_type=ResourceType.HOSTED_APPLICATION,
            resource_id=rid,
            principal_type=PrincipalType.USER,
            principal_id=uid,
            permission_level=svc.Action.READ,
            grant_source="link",
            source_link_id=link_id,
        )
    )
    pg_session.flush()


def test_link_grant_on_an_existing_name_row_leaves_it_name(pg_session):
    from types import SimpleNamespace
    from uuid import uuid4

    from codemie.rest_api.models.permission import Permission, ResourceType
    from codemie.rest_api.security.user import User
    from codemie.service.permission import named_grant_service as svc

    (uid,) = _seed_users(pg_session, 1)
    rid = uuid4()
    pg_session.execute(svc.upsert_statement(ResourceType.HOSTED_APPLICATION, rid, None, [uid]))
    resource = SimpleNamespace(id=rid)
    written = svc.link_grant(
        pg_session, ResourceType.HOSTED_APPLICATION, resource, User(id=uid, username=uid), uuid4(), None
    )
    assert written is False
    row = pg_session.query(Permission).filter(Permission.resource_id == rid).one()
    assert row.grant_source == "name" and row.source_link_id is None


def test_upgraded_link_row_becomes_name_and_survives_link_removal(pg_session):
    from uuid import uuid4

    from codemie.rest_api.models.permission import Permission, ResourceType
    from codemie.service.permission import named_grant_service as svc

    ids = _seed_users(pg_session, 2)
    rid = uuid4()
    link_id = uuid4()
    _link_row(pg_session, rid, ids[0], link_id)
    _link_row(pg_session, rid, ids[1], link_id)
    pg_session.execute(svc.upsert_statement(ResourceType.HOSTED_APPLICATION, rid, None, [ids[0]]))
    svc.remove_link_grants(None, ResourceType.HOSTED_APPLICATION, rid, link_id, None, pg_session)
    left = pg_session.query(Permission).filter(Permission.resource_id == rid).all()
    assert [(r.principal_id, r.grant_source, r.source_link_id) for r in left] == [(ids[0], "name", None)]


@pytest.mark.parametrize("count", [1000, 10000])
def test_timing_remove_all_grants(pg_session, count):
    from uuid import uuid4

    from codemie.rest_api.models.permission import ResourceType
    from codemie.service.permission import named_grant_service as svc

    ids = _seed_users(pg_session, count)
    rid = uuid4()
    pg_session.execute(svc.upsert_statement(ResourceType.HOSTED_APPLICATION, rid, None, ids))
    started = time.perf_counter()
    svc.remove_all_grants(None, ResourceType.HOSTED_APPLICATION, rid, None, pg_session)
    sys.stderr.write(f"remove_all_grants x{count}: {time.perf_counter() - started:.3f}s\n")


def test_timing_100_id_share(pg_session):
    from uuid import uuid4

    from codemie.rest_api.models.permission import ResourceType
    from codemie.service.permission import named_grant_service as svc

    ids = _seed_users(pg_session, 100)
    started = time.perf_counter()
    from types import SimpleNamespace

    from codemie.rest_api.security.user import User

    owner = User(id="timing-owner", username="timing-owner")
    resource = SimpleNamespace(id=uuid4(), created_by=SimpleNamespace(id=owner.id), security_state="clear")
    svc.grant(
        pg_session,
        resource_type=ResourceType.HOSTED_APPLICATION,
        resource=resource,
        owner=owner,
        project=None,
        user_ids=ids,
    )
    sys.stderr.write(f"grant x100: {time.perf_counter() - started:.3f}s\n")
