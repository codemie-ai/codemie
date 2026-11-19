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

"""Load check for the IdP-sync user list at 60,000 users (EPMCDME-16895, AC11).

Seeds 60,000 users with 5 memberships each (300,000 ``user_projects`` rows, 500 shared
projects) into a real PostgreSQL database and asserts that a page of 100 users, read through
``IdpSyncService.list_users``, takes under one second. Pages at the start, the middle and the
end of the id order are measured, since ``OFFSET`` cost grows with the page number.

Not part of the default run: it is marked ``performance`` and is skipped unless
``IDP_SYNC_LOAD_PG_URL`` points to an empty, disposable PostgreSQL database. The test creates
and drops its own tables in that database, so never point it at a real one. Run it with::

    docker run -d --rm --name idp-load-pg -e POSTGRES_PASSWORD=pg -p 55432:5432 postgres:16
    IDP_SYNC_LOAD_PG_URL=postgresql://postgres:pg@localhost:55432/postgres \\
        poetry run pytest -o addopts=--import-mode=importlib -m performance \\
        tests/performance/test_idp_sync_list_load.py

Measured (PostgreSQL 16 in a container on a laptop, no extra index beyond the primary keys and
indexes the models declare): see the ``MEASURED`` constant below.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert
from sqlmodel import Session, SQLModel

from codemie.core.models import Application, CostCenter
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.service.user.idp_sync_service import IdpSyncService

PG_URL = os.environ.get("IDP_SYNC_LOAD_PG_URL")

USERS = 60_000
MEMBERSHIPS_PER_USER = 5
PROJECTS = 500
PER_PAGE = 100
LIMIT_SECONDS = 1.0
CHUNK = 5_000

MEASURED = "page 0: 47 ms, page 300: 13 ms, page 599: 13 ms (limit 1000 ms); no index or migration needed"

pytestmark = [
    pytest.mark.performance,
    pytest.mark.skipif(not PG_URL, reason="IDP_SYNC_LOAD_PG_URL is not set (disposable PostgreSQL required)"),
]

_TABLES = [UserDB.__table__, UserProject.__table__, CostCenter.__table__, Application.__table__]


@pytest.fixture(scope="module")
def session():
    engine = create_engine(PG_URL)
    with engine.begin() as connection:  # the applications.name index uses trigram operators
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    SQLModel.metadata.drop_all(engine, tables=_TABLES)
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    now = datetime.now(UTC)

    with engine.begin() as connection:
        connection.execute(
            insert(Application.__table__),
            [
                {
                    "id": f"project-{p:04d}",
                    "name": f"project-{p:04d}",
                    "project_type": Application.ProjectType.SHARED,
                    "date": now,
                    "update_date": now,
                }
                for p in range(PROJECTS)
            ],
        )
        for start in range(0, USERS, CHUNK):
            ids = range(start, min(start + CHUNK, USERS))
            connection.execute(
                insert(UserDB.__table__),
                [
                    {
                        "id": f"{n:036d}",
                        "username": f"user{n}",
                        "email": f"user{n}@example.com",
                        "user_type": "regular",
                        "is_active": n % 50 != 0,  # a few deactivated users in the mix
                        "is_admin": False,
                        "is_maintainer": False,
                        "is_auditor": False,
                        "auth_source": "keycloak",
                        "email_verified": True,
                        "deleted_at": None if n % 50 != 0 else now,
                        "date": now,
                        "update_date": now,
                    }
                    for n in ids
                ],
            )
            connection.execute(
                insert(UserProject.__table__),
                [
                    {
                        "id": f"{n:036d}-{k}",
                        "user_id": f"{n:036d}",
                        "project_name": f"project-{(n * 7 + k * 101) % PROJECTS:04d}",
                        "is_project_admin": k == 0,
                        "is_default": False,
                        "date": now,
                        "update_date": now,
                    }
                    for n in ids
                    for k in range(MEMBERSHIPS_PER_USER)
                ],
            )
        connection.exec_driver_sql("ANALYZE")

    with Session(engine) as db:
        yield db

    engine.dispose()
    cleanup = create_engine(PG_URL)
    SQLModel.metadata.drop_all(cleanup, tables=_TABLES)
    cleanup.dispose()


@pytest.mark.parametrize("page", [0, USERS // PER_PAGE // 2, USERS // PER_PAGE - 1], ids=["first", "middle", "last"])
def test_list_page_of_100_at_60000_users_takes_under_one_second(session, page):
    started = time.perf_counter()
    result = IdpSyncService.list_users(session, page, PER_PAGE)
    elapsed = time.perf_counter() - started

    assert result.total == USERS
    assert len(result.items) == PER_PAGE
    assert all(len(item.memberships) == MEMBERSHIPS_PER_USER for item in result.items)
    assert elapsed < LIMIT_SECONDS, f"page {page} took {elapsed:.3f}s (limit {LIMIT_SECONDS}s)"
