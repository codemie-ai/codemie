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

"""ConsentGate (EPMCDME-16849 spec item 3): exactly the header's project, any class and user type."""

# ruff: noqa: F811  (the `world` fixture is imported from the destination tests)

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.ports import (
    PublicationRejectedError,
    SessionBinding,
    TelemetryStorageUnavailableError,
)
from codemie.service.analytics.cli_analytics_project_gate import ConsentGate, EvidenceGate, build_evidence_gate
from tests.codemie.service.analytics.test_cli_analytics_destination import (  # noqa: F401 (fixtures)
    EMAIL,
    MODULE,
    PERSONAL,
    app,
    make_user,
    world,
)

CLASSES = ["codemie", "corporate", "personal", "unknown"]
BOUND = SessionBinding("Linked", "corporate", "linked_folder", "", contract_version="1", work_consent="explicit-folder")


def gate(user=None, *, subscription="corporate", header="Linked", source="profile", remote="git@h:o/r", **contract):
    contract = contract or {"contract_version": "1", "work_consent": "explicit-folder"}
    return build_evidence_gate(
        user or make_user(default_project="Default"),
        header_project=header,
        project_source=source,
        repo_remote=remote,
        subscription_type=subscription,
        client_subscription_type=subscription,
        client_policy_version="v1",
        **contract,
    )


async def rejected(awaitable) -> tuple[int, str, str]:
    with pytest.raises(PublicationRejectedError) as caught:
        await awaitable
    return caught.value.status_code, caught.value.reason, caught.value.disposition


def test_build_selects_the_consent_gate_only_for_contract_one_with_consent():
    user = make_user()
    assert isinstance(gate(user), ConsentGate)
    assert type(gate(user, contract_version="1", work_consent=None)) is EvidenceGate
    assert type(gate(user, contract_version=None, work_consent=None)) is EvidenceGate


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", CLASSES)
@pytest.mark.parametrize("user_type", ["regular", "external", "service_account"])
async def test_an_unbound_usable_project_is_bound_canonically_for_any_class_and_user_type(
    world, subscription, user_type
):
    consent_gate = gate(make_user(user_type=user_type), subscription=subscription, header="lINKED")

    outcome = await consent_gate.resolve_session(None, None, None)

    assert isinstance(outcome, SessionBinding) and consent_gate.binding is outcome
    assert (outcome.project_name, outcome.project_origin, outcome.subscription_type) == (
        "Linked",
        "linked_folder",
        subscription,
    )
    assert (outcome.contract_version, outcome.work_consent) == ("1", "explicit-folder")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "profile", "linked_folder", "bogus"])
async def test_the_project_source_header_is_ignored(world, source):
    outcome = await gate(source=source).resolve_session(None, None, "git@h:o/r")
    assert outcome.project_name == "Linked" and outcome.project_origin == "linked_folder"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "deleted", "access_lost", "membership_lost", "other_personal"])
async def test_an_unusable_unbound_project_is_a_project_unavailable_hold_with_no_fallback(world, change):
    user = make_user(default_project="Default")
    if change == "missing":
        world.remove("Linked")
    elif change == "deleted":
        world.projects["linked"].deleted_at = datetime.now()
    elif change == "access_lost":
        user = make_user(project_names=["Default"])
    elif change == "membership_lost":
        world.membership.return_value = None
    else:
        world.projects["linked"] = app("Linked", project_type=PERSONAL, created_by="someone-else")

    assert await rejected(gate(user).resolve_session(None, None, None)) == (503, "project_unavailable", "hold")
    world.ensure.assert_not_awaited()  # nothing is provisioned, no personal/default fallback


@pytest.mark.asyncio
async def test_a_missing_header_is_a_project_unavailable_hold(world):
    assert await rejected(gate(header=None).resolve_session(None, None, None)) == (503, "project_unavailable", "hold")


@pytest.mark.asyncio
@pytest.mark.parametrize("user_type", ["regular", "external"])
async def test_a_usable_personal_type_project_is_a_final_422(world, user_type):
    world.projects["linked"] = app("Linked", project_type=PERSONAL, created_by="u-1")
    user = make_user(user_type=user_type, project_names=["Linked"])

    assert await rejected(gate(user).resolve_session(None, None, None)) == (422, "consent_personal_project", "final")


@pytest.mark.asyncio
async def test_a_project_without_analytics_enabled_is_a_hold(world):
    world.projects["linked"].cli_analytics_enabled = False

    assert await rejected(gate().resolve_session(None, None, None)) == (503, "project_analytics_not_enabled", "hold")


@pytest.mark.asyncio
async def test_a_failed_lookup_is_the_existing_retryable_error(world):
    with patch(f"{MODULE}.application_repository.aget_active_by_name_case_insensitive", side_effect=RuntimeError("x")):
        with pytest.raises(TelemetryStorageUnavailableError):
            await gate().resolve_session(None, None, None)


@pytest.mark.asyncio
async def test_an_unbound_session_with_legacy_rows_of_another_project_conflicts_and_never_adopts(world):
    assert await gate().resolve_session(None, "Other", None) == "session_project_conflict"


@pytest.mark.asyncio
async def test_an_unbound_session_with_legacy_rows_of_the_same_project_is_bound_there(world):
    outcome = await gate().resolve_session(None, "linked", None)
    assert isinstance(outcome, SessionBinding) and outcome.project_name == "Linked"


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", CLASSES)
async def test_a_bound_session_is_checked_against_the_request_not_re_resolved(world, subscription):
    outcome = await gate(subscription=subscription, header="LINKED").resolve_session(BOUND, None, None)

    assert isinstance(outcome, SessionBinding) and outcome.project_name == "Linked"
    assert (outcome.project_origin, outcome.work_consent) == ("linked_folder", "explicit-folder")
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ((None, None), "session_contract_conflict"),
        (("1", None), "session_contract_conflict"),
        (("1", "explicit-folder"), None),
    ],
)
async def test_a_contract_or_consent_mismatch_of_a_bound_session_is_a_contract_conflict(world, stored, expected):
    bound = SessionBinding("Linked", "corporate", "linked_folder", contract_version=stored[0], work_consent=stored[1])
    outcome = await gate().resolve_session(bound, None, None)
    assert (outcome if isinstance(outcome, str) else None) == expected


@pytest.mark.asyncio
async def test_a_header_naming_another_project_is_a_project_conflict_and_no_personal_anchor_check_runs(world):
    assert await gate(header="Other").resolve_session(BOUND, None, None) == "session_project_conflict"
    personal = SessionBinding(EMAIL, "personal", "personal", contract_version="1", work_consent="explicit-folder")
    assert await gate(header="Linked").resolve_session(personal, None, None) == "session_project_conflict"


@pytest.mark.asyncio
async def test_a_bound_destination_that_is_gone_disabled_or_revoked_is_a_final_discard(world):
    world.projects["linked"].cli_analytics_enabled = False
    assert await gate().resolve_session(BOUND, None, None) == "cli_analytics_disabled"

    world.projects["linked"].cli_analytics_enabled = True
    world.membership.return_value = None
    assert await gate().resolve_session(BOUND, None, None) == "session_project_unavailable"

    world.remove("Linked")
    assert await gate().resolve_session(BOUND, None, None) == "session_project_unavailable"


@pytest.mark.asyncio
async def test_the_context_conflict_check_is_kept(world):
    consent_gate = gate(header="Linked")
    assert await consent_gate(frozenset({"Other"})) == "project_context_conflict"
    assert await consent_gate(frozenset({"linked"})) is None
