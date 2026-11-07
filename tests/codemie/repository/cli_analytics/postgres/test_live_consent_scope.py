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

"""Live-Postgres read path of a consent-bound session (EPMCDME-16849 AC1).

A consented session of any class is stored under the canonical project with origin `linked_folder`: the real reader
lists and details it (meta, cost, scalars, tools, events, dispatches, skills) for super admins and that project's
admins, and denies another project's admin and a non-admin, while an owner-only personal session stays private.
Skipped when TEST_LOCAL_PG_URL is unreachable (same harness as test_live_owner_scope)."""

# ruff: noqa: F811  (the `case` fixture is imported)

import pytest

from tests.codemie.repository.cli_analytics.postgres.test_live_owner_scope import (  # noqa: F401 (fixtures)
    OWNER,
    _scope,
    case,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("billing_class", ["codemie", "corporate", "personal", "unknown"])
async def test_a_consented_session_is_read_by_super_and_project_admins_only(case, billing_class):
    sid = await case.seed("n1", sub=billing_class, origin="linked_folder", owner=OWNER, skill="s1")
    allowed = [
        _scope("detail", "super@x.com", unrestricted=True),
        _scope("detail", "pa@x.com", projects=(case.project,)),
    ]
    denied = [
        _scope("detail", "other-pa@x.com", projects=("someone-elses-project",)),
        _scope("detail", "ordinary@x.com"),
        _scope("detail", OWNER),  # the owner reads it only if admin: no owner access is introduced
    ]
    for scope in allowed:
        await case.assert_allowed(sid, scope)
    for scope in denied:
        await case.assert_denied(sid, scope)
    assert sid in await case.listed(_scope("exclude", "super@x.com", unrestricted=True))
    assert sid in await case.listed(_scope("exclude", "pa@x.com", projects=(case.project,)))
    assert sid not in await case.listed(_scope("exclude", "other-pa@x.com", projects=("someone-elses-project",)))
    assert sid not in await case.listed(_scope("own_only", OWNER))
    events = await case.reader.get_session_detail_events(sid, scope=allowed[1])
    assert events is not None


@pytest.mark.asyncio
async def test_the_private_owner_scope_is_unchanged_next_to_a_consented_session(case):
    private = await case.seed("p", sub="personal", origin="personal", owner=OWNER)
    consented = await case.seed("n", sub="personal", origin="linked_folder", owner=OWNER)

    assert await case.listed(_scope("own_only", OWNER)) == {private}
    await case.assert_allowed(private, _scope("detail", OWNER))
    await case.assert_denied(private, _scope("detail", "super@x.com", unrestricted=True))
    assert private not in await case.listed(_scope("exclude", "super@x.com", unrestricted=True))
    assert consented in await case.listed(_scope("exclude", "super@x.com", unrestricted=True))
