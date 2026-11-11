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

"""Snapshots past the trust window are deleted by last login (EPMCDME-16625 AR-9)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from codemie.repository.idp_user_snapshot_repository import idp_user_snapshot_repository


@pytest.mark.asyncio
async def test_delete_last_seen_before_deletes_by_last_login():
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=3))
    cutoff = datetime(2026, 11, 8, 12, 0)

    deleted = await idp_user_snapshot_repository.adelete_last_seen_before(session, cutoff)

    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    sql = " ".join(str(compiled).split())
    assert deleted == 3
    assert sql.startswith("DELETE FROM idp_user_snapshots WHERE")
    assert "coalesce(idp_user_snapshots.update_date, idp_user_snapshots.date) < '2026-11-08 12:00:00'" in sql
