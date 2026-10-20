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

"""The CLI analytics ingest gate: who may send for which project, and whether it is enabled (EPMCDME-16025)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from codemie.configs import config
from codemie.core.models import Application
from codemie.repository.cli_analytics.ports import TelemetryStorageUnavailableError, accepted_project
from codemie.rest_api.security.user import User
from codemie.service.analytics.cli_analytics_project_gate import build_ingest_gate

MODULE = "codemie.service.analytics.cli_analytics_project_gate"


def make_user(**kwargs) -> User:
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="u-1", email="u@example.com", **kwargs)


def app(name: str = "Proj-A", enabled: bool = True, deleted: bool = False) -> Application:
    return Application(
        id=name, name=name, cli_analytics_enabled=enabled, deleted_at=datetime.now() if deleted else None
    )


@pytest.fixture
def lookup():
    """Patches the DB policy lookup; `lookup.return_value` is the Application (or None)."""

    @asynccontextmanager
    async def session():
        yield object()

    with (
        patch(f"{MODULE}.get_async_session", session),
        patch(f"{MODULE}.application_repository.aget_active_by_name_case_insensitive", new_callable=AsyncMock) as mock,
    ):
        mock.return_value = app()
        yield mock


MEMBER = {"project_names": ["Proj-A"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "", "   "])
async def test_missing_or_blank_header_is_project_context_required(lookup, header):
    gate = build_ingest_gate(make_user(**MEMBER), header)

    assert await gate(frozenset()) == "project_context_required"
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_project_for_a_member_is_stored(lookup):
    assert await build_ingest_gate(make_user(**MEMBER), "Proj-A")(frozenset()) is None


@pytest.mark.asyncio
async def test_header_is_matched_case_insensitively_and_looked_up_as_received(lookup):
    gate = build_ingest_gate(make_user(**MEMBER), "proj-a")

    assert await gate(frozenset({"PROJ-A", "proj-a"})) is None
    lookup.assert_awaited_once()
    assert lookup.await_args.args[1] == "proj-a"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "body"),
    [("Proj-A", "Proj-A "), (" Proj-A", "Proj-A"), ("ss", "ß")],
    ids=["trailing-space", "leading-space", "ss-vs-sharp-s"],
)
async def test_pair_differing_beyond_lower_is_a_conflict_not_normalized_away(lookup, header, body):
    gate = build_ingest_gate(make_user(**MEMBER), header)

    assert await gate(frozenset({body})) == "project_context_conflict"
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_pair_differing_only_by_case_is_the_same_project_the_lookup_resolves(lookup):
    gate = build_ingest_gate(make_user(**MEMBER), "PROJ-a")

    assert await gate(frozenset({"proj-A"})) is None
    assert lookup.await_args.args[1] == "PROJ-a"


@pytest.mark.asyncio
async def test_disabled_project_is_cli_analytics_disabled(lookup):
    lookup.return_value = app(enabled=False)

    assert await build_ingest_gate(make_user(**MEMBER), "Proj-A")(frozenset()) == "cli_analytics_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [None, app(deleted=True)], ids=["unknown", "soft-deleted"])
async def test_unknown_or_deleted_project_is_context_required(lookup, found):
    lookup.return_value = found

    assert await build_ingest_gate(make_user(**MEMBER), "Proj-A")(frozenset()) == "project_context_required"


@pytest.mark.asyncio
async def test_sender_without_access_is_context_required_even_if_enabled(lookup):
    assert await build_ingest_gate(make_user(project_names=["other"]), "Proj-A")(frozenset()) == (
        "project_context_required"
    )


@pytest.mark.asyncio
async def test_global_admin_needs_no_membership(lookup):
    assert await build_ingest_gate(make_user(is_admin=True, project_names=[]), "Proj-A")(frozenset()) is None


@pytest.mark.asyncio
async def test_one_conflicting_value_among_matching_ones_is_a_conflict(lookup):
    gate = build_ingest_gate(make_user(**MEMBER), "Proj-A")

    assert await gate(frozenset({"proj-a", "Proj-B"})) == "project_context_conflict"
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_two_distinct_values_conflict_even_without_the_header_project(lookup):
    gate = build_ingest_gate(make_user(**MEMBER), "Proj-A")

    assert await gate(frozenset({"p1", "p2"})) == "project_context_conflict"


@pytest.mark.asyncio
async def test_a_lookup_failure_is_a_storage_unavailable_error(lookup):
    lookup.side_effect = RuntimeError("db down")

    with pytest.raises(TelemetryStorageUnavailableError):
        await build_ingest_gate(make_user(**MEMBER), "Proj-A")(frozenset())


# ── the project a request is accepted for (EPMCDME-16077) ─────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "explicit"),
    [
        ("QA_CASE_CHECK", frozenset()),
        ("qa_case_check", frozenset({"QA_CASE_CHECK"})),
        ("Qa_Case_Check", frozenset({"qa_case_check"})),
    ],
    ids=["upper-header", "upper-body", "mixed-pair"],
)
async def test_an_accepted_request_reports_the_stored_name_of_the_project(lookup, header, explicit):
    lookup.return_value = app("qa_case_check")  # project names are stored lower-case
    gate = build_ingest_gate(make_user(project_names=["qa_case_check"]), header)
    assert accepted_project(gate) is None  # nothing is decided before the gate runs

    assert await gate(explicit) is None

    assert accepted_project(gate) == "qa_case_check"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("found", "member", "header", "explicit", "reason"),
    [
        (app("proj-a", enabled=False), ["proj-a"], "PROJ-A", frozenset(), "cli_analytics_disabled"),
        (None, ["proj-a"], "PROJ-A", frozenset(), "project_context_required"),
        (app("proj-a"), ["other"], "PROJ-A", frozenset(), "project_context_required"),
        (app("proj-a"), ["proj-a"], "PROJ-A", frozenset({"proj-b"}), "project_context_conflict"),
        (app("proj-a"), ["proj-a"], " PROJ-A ", frozenset({"proj-a"}), "project_context_conflict"),
        (app("proj-a"), ["proj-a"], "", frozenset(), "project_context_required"),
    ],
    ids=["disabled", "unknown", "no-access", "other-project", "padded", "blank"],
)
async def test_a_discarded_request_reports_no_project(lookup, found, member, header, explicit, reason):
    lookup.return_value = found
    gate = build_ingest_gate(make_user(project_names=member), header)

    assert await gate(explicit) == reason

    assert accepted_project(gate) is None


@pytest.mark.asyncio
async def test_a_lookup_failure_reports_no_project_and_still_raises(lookup):
    lookup.side_effect = RuntimeError("db down")
    gate = build_ingest_gate(make_user(**MEMBER), "PROJ-A")

    with pytest.raises(TelemetryStorageUnavailableError):
        await gate(frozenset())

    assert accepted_project(gate) is None


def test_a_plain_gate_names_no_project():
    async def plain(_explicit):
        return None

    assert accepted_project(plain) is None
    assert accepted_project(None) is None
