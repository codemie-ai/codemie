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

"""The publication-contract columns of session_dims and their binding fields (subscription analytics capability)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codemie.repository.cli_analytics.ports import SessionBinding
from codemie.repository.cli_analytics.postgres import session_binding as sb
from tests.codemie.repository.cli_analytics.postgres.test_migrations import (
    SESSION_ROUTING,
    _load_revision,
    _normalized,
)

CONTRACT = _load_revision("a1c1a0000010_cli_analytics_publication_contract.py")
_NEW = ["contract_version", "work_consent", "funding_route"]


def _row(**extra):
    return {
        "owner_email": "a@x.com",
        "project_name": "p",
        "subscription_type": "personal",
        "project_origin": "linked_folder",
        "route_note": "",
        **extra,
    }


def test_revision_follows_the_routing_head_and_adds_only_nullable_text_columns() -> None:
    assert CONTRACT.down_revision == SESSION_ROUTING.revision
    assert [_normalized(s) for s in CONTRACT.UPGRADE] == [
        "ALTER TABLE session_dims ADD COLUMN contract_version text, ADD COLUMN work_consent text, "
        "ADD COLUMN funding_route text"
    ]
    assert [_normalized(s) for s in CONTRACT.DOWNGRADE] == [
        "ALTER TABLE session_dims DROP COLUMN contract_version, DROP COLUMN work_consent, DROP COLUMN funding_route"
    ]


def test_a_stored_binding_returns_the_three_fields() -> None:
    b = sb.stored_binding(_row(contract_version="1", work_consent="no", funding_route="codemie"))

    assert (b.contract_version, b.work_consent, b.funding_route) == ("1", "no", "codemie")


def test_a_legacy_row_reads_none() -> None:
    b = sb.stored_binding(_row(contract_version=None, work_consent=None, funding_route=None))

    assert (b.contract_version, b.work_consent, b.funding_route) == (None, None, None)


def test_a_row_without_the_keys_and_an_old_construction_still_work() -> None:
    assert sb.stored_binding(_row()).contract_version is None
    assert SessionBinding("p", "personal").funding_route is None


def test_the_read_and_write_sql_name_the_new_columns() -> None:
    for column in _NEW:
        assert column in sb.BINDINGS_SQL
        assert column in sb._BINDING_COLUMNS


@pytest.mark.asyncio
async def test_first_binding_writes_the_triple_and_none_for_an_old_binding() -> None:
    conn = AsyncMock()
    new = SessionBinding(
        "p", "personal", "linked_folder", "", contract_version="1", work_consent="no", funding_route="x"
    )
    old = SessionBinding("p", "personal", "linked_folder", "")

    await sb.bind(conn, {"s": new}, ["s"], [], "a@x.com")
    await sb.bind(conn, {"s": old}, ["s"], [], "a@x.com")

    assert conn.execute.await_args_list[0].args[-3:] == ("1", "no", "x")
    assert conn.execute.await_args_list[1].args[-3:] == (None, None, None)
