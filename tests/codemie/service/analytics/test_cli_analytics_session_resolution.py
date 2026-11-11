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

"""EvidenceGate.resolve_session (EPMCDME-16440 review fix-up): the destination chosen under the session lock,
the hook-row repo_remote fallback and the routing counters. The database lookups are the World fixture's."""

# ruff: noqa: F811  (the `world` fixture is imported from the destination tests)

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from codemie.configs import config
from codemie.repository.cli_analytics.ports import (
    SessionBinding,
    TelemetryStorageUnavailableError,
    gate_binding,
    gate_resolver,
    gate_validator,
)
from codemie.repository.cli_analytics.postgres.session_binding import (
    SessionDiscard,
    _resolve_session,
    private_personal,
)
from codemie.repository.cli_analytics.scope import SHARED_ORIGINS, normalise_project_origin
from codemie.service.analytics.cli_analytics_project_gate import (
    DESTINATION_EVENTS,
    build_evidence_gate,
    build_ingest_gate,
)
from tests.codemie.service.analytics.test_cli_analytics_destination import (  # noqa: F401 (fixtures)
    EMAIL,
    MODULE,
    app,
    make_user,
    world,
)

LINKED = SessionBinding("Linked", "corporate", "linked_folder", "")


def gate(user=None, *, subscription="corporate", remote=None, header=None, source=None, **contract):
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


@pytest.fixture(autouse=True)
def _fresh_counters():
    DESTINATION_EVENTS.clear()
    yield
    DESTINATION_EVENTS.clear()


@pytest.mark.asyncio
async def test_the_evidence_gate_answers_the_sender_hash_a_discard_log_reads(world):
    """EPMCDME-16441 reads `gate.user_hash` when it logs a discard; a context conflict must not raise there. No
    key -> None; a configured key -> the same keyed value as the legacy gate for the same sender."""
    evidence_gate = gate(header="One")

    assert await evidence_gate(frozenset({"Two"})) == "project_context_conflict"
    with patch.object(config, "CLI_ANALYTICS_LOG_HMAC_KEY", "", create=True):
        assert evidence_gate.user_hash is None
    with patch.object(config, "CLI_ANALYTICS_LOG_HMAC_KEY", "k", create=True):
        legacy = build_ingest_gate(evidence_gate._user, "One")
        assert evidence_gate.user_hash is not None
        assert evidence_gate.user_hash == legacy.user_hash


@pytest.mark.asyncio
async def test_an_accepted_evidence_request_never_computes_the_user_hash(world):
    evidence_gate = gate(header="One")

    with (
        patch.object(config, "CLI_ANALYTICS_LOG_HMAC_KEY", "k", create=True),
        patch("codemie.service.analytics.cli_analytics_project_gate.hmac.new", side_effect=AssertionError("HMAC")),
    ):
        assert await evidence_gate(frozenset()) is None


@pytest.mark.asyncio
async def test_a_bound_session_is_validated_alone_so_a_broken_default_does_not_touch_it(world):
    world.remove("Default")  # the candidate would be a retryable 503

    outcome = await gate().resolve_session(LINKED, None, None)

    assert isinstance(outcome, SessionBinding) and outcome.project_name == "Linked"
    assert (outcome.project_origin, outcome.client_policy_version) == ("linked_folder", "v1")


@pytest.mark.asyncio
async def test_a_bound_project_that_is_disabled_or_gone_is_judged_on_its_own(world):
    world.projects["linked"].cli_analytics_enabled = False
    assert await gate().resolve_session(LINKED, None, None) == "cli_analytics_disabled"

    world.remove("Linked")
    assert await gate().resolve_session(LINKED, None, None) == "session_project_unavailable"


@pytest.mark.asyncio
async def test_an_unbound_legacy_project_that_is_gone_is_still_retried(world):
    """Only an ALREADY BOUND session is finally discarded; a legacy project is retried (nothing was bound yet)."""
    world.remove("Linked")
    with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
        await gate().resolve_session(None, "Linked", None)


@pytest.mark.asyncio
async def test_a_failed_lookup_of_a_bound_project_is_retried_not_discarded(world):
    with patch(f"{MODULE}.application_repository.aget_active_by_name_case_insensitive", side_effect=RuntimeError("db")):
        with pytest.raises(TelemetryStorageUnavailableError):
            await gate().resolve_session(LINKED, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["renamed", "deleted", "soft_deleted", "access_lost", "membership_lost"])
async def test_a_bound_project_that_changed_is_a_final_discard_and_is_never_rerouted(world, change):
    """The binding holds the project NAME, so a renamed, deleted or no longer accessible project is the final
    discard `session_project_unavailable` (HTTP 200, the CLI acknowledges it). History stays; nothing is rerouted
    to the default or a renamed project, migrated, provisioned or written. Access can come back, but the CLI
    already dropped that range: the next request for the session is accepted again."""
    user = make_user(default_project="Default")
    if change == "renamed":
        world.projects["renamed"] = world.projects.pop("linked")
        world.projects["renamed"].name = "Renamed"
    elif change == "deleted":
        world.remove("Linked")
    elif change == "soft_deleted":
        world.projects["linked"] = app("Linked", deleted=True)
    elif change == "access_lost":
        user = make_user(default_project="Default", project_names=["Default"])
    else:
        world.membership.side_effect = lambda _s, _u, name: None if name == "Linked" else object()
    evidence_gate = gate(user)

    with patch(f"{MODULE}.resolve_destination", side_effect=AssertionError("rerouted")) as candidate:
        assert await evidence_gate.resolve_session(LINKED, None, None) == "session_project_unavailable"

    candidate.assert_not_called()
    assert evidence_gate.binding is None
    world.ensure.assert_not_awaited()
    if change == "access_lost":  # the access comes back, the same session is accepted again
        outcome = await gate().resolve_session(LINKED, None, None)
        assert isinstance(outcome, SessionBinding) and outcome.project_name == "Linked"


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor", ["bound", "legacy"])
async def test_a_personal_request_on_a_shared_session_is_a_conflict_without_provisioning(world, anchor):
    world.remove(EMAIL)  # the personal project does not exist yet: it must not be created for a discard
    bound, legacy = (LINKED, None) if anchor == "bound" else (None, "Linked")

    outcome = await gate(subscription="personal").resolve_session(bound, legacy, None)

    assert outcome == "session_project_conflict"
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_personal_request_on_the_owners_own_personal_session_proceeds(world):
    personal = SessionBinding(EMAIL, "personal", "personal", "")
    world.projects[EMAIL].cli_analytics_enabled = False  # a personal destination needs no flag

    outcome = await gate(subscription="personal").resolve_session(personal, None, None)

    assert isinstance(outcome, SessionBinding) and outcome.project_name == EMAIL


@pytest.mark.asyncio
async def test_an_unbound_session_resolves_the_candidate_as_before(world):
    outcome = await gate(header="Linked", source="profile").resolve_session(None, None, None)

    assert (outcome.project_name, outcome.project_origin) == ("Linked", "profile")


@pytest.mark.asyncio
async def test_an_unbound_legacy_session_adopts_its_project_without_resolving_a_candidate(world):
    """Every fresh candidate would fail (no default, lookup error, header project gone); none may be consulted."""
    user = make_user(default_project=None, project_names=["Linked"])
    with patch(f"{MODULE}.resolve_destination", side_effect=AssertionError("candidate resolved")) as candidate:
        evidence_gate = gate(user, header="Gone", source="profile", remote="git@h:o/r")
        outcome = await evidence_gate.resolve_session(None, "linked", None)

    candidate.assert_not_called()
    assert isinstance(outcome, SessionBinding) and evidence_gate.binding is outcome
    assert (outcome.project_name, outcome.subscription_type, outcome.project_origin) == (
        "linked",
        "corporate",
        "legacy_project",
    )
    assert (outcome.client_subscription_type, outcome.client_policy_version) == ("corporate", "v1")


@pytest.mark.asyncio
async def test_an_unbound_legacy_session_is_still_validated_against_its_actual_project(world):
    world.projects["linked"].cli_analytics_enabled = False
    assert await gate().resolve_session(None, "Linked", None) == "cli_analytics_disabled"

    world.projects["linked"].cli_analytics_enabled = True
    world.membership.return_value = None  # the owner lost live membership
    with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
        await gate().resolve_session(None, "Linked", None)

    world.membership.return_value = object()
    world.remove("Linked")
    with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
        await gate().resolve_session(None, "Linked", None)


@pytest.mark.asyncio
async def test_an_unbound_legacy_session_never_adopts_another_users_personal_project(world):
    world.projects[EMAIL].created_by = "someone-else"

    with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
        await gate().resolve_session(None, EMAIL, None)


@pytest.mark.asyncio
async def test_a_personal_request_on_an_unbound_legacy_session_still_resolves_its_own_project(world):
    outcome = await gate(subscription="personal").resolve_session(None, EMAIL, None)

    assert isinstance(outcome, SessionBinding) and (outcome.project_name, outcome.project_origin) == (EMAIL, "personal")


@pytest.mark.asyncio
async def test_the_hook_rows_repo_remote_routes_when_the_header_carries_none(world):
    world.registrations = ["Registered"]
    seen: list[str] = []
    with patch(f"{MODULE}._registrations", side_effect=lambda r: seen.append(r) or []):
        await gate(remote=None).resolve_session(None, None, "git@h:o/hook-repo")
        await gate(remote="git@h:o/header-repo").resolve_session(None, None, "git@h:o/hook-repo")

    assert seen == ["git@h:o/hook-repo", "git@h:o/header-repo"]  # the header wins when both exist


@pytest.mark.asyncio
async def test_a_registered_repository_from_a_hook_row_beats_the_default(world):
    world.registrations = ["Registered"]

    outcome = await gate().resolve_session(None, None, "git@h:o/r")

    assert (outcome.project_name, outcome.project_origin) == ("Registered", "registered_repository")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare", "note"),
    [
        (lambda w: None, "folder_not_linked"),
        (lambda w: w.remove("Registered") or w.registrations.append("Registered"), "registered_project_inaccessible"),
        (lambda w: w.registrations.extend(["Registered", "Other"]), "registration_ambiguous"),
    ],
)
async def test_every_route_note_is_counted(world, prepare, note):
    prepare(world)

    outcome = await gate().resolve_session(None, None, "git@h:o/r")

    assert outcome.route_note == note
    assert DESTINATION_EVENTS[f"route_note:{note}"] == 1


@pytest.mark.asyncio
async def test_a_linked_project_that_is_unavailable_is_counted(world):
    world.remove("Linked")

    outcome = await gate(header="Linked", source="linked_folder").resolve_session(None, None, None)

    assert outcome.route_note == "linked_project_unavailable"
    assert DESTINATION_EVENTS["route_note:linked_project_unavailable"] == 1


@pytest.mark.asyncio
async def test_default_project_unavailable_is_counted_as_a_retry_for_no_project_and_a_broken_default(world):
    with patch("codemie.rest_api.security.user.User.effective_default_project", return_value=None):
        with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
            await gate().resolve_session(None, None, None)
    assert DESTINATION_EVENTS["default_project_unavailable:retry"] == 1

    world.remove("Default")
    with pytest.raises(TelemetryStorageUnavailableError, match="default_project_unavailable"):
        await gate().resolve_session(None, None, None)
    assert DESTINATION_EVENTS["default_project_unavailable:retry"] == 2
    assert "default_project_unavailable:discard" not in DESTINATION_EVENTS
    world.ensure.assert_not_awaited()  # nothing is provisioned on a shared route


# --- Personal-paid sessions of user types without a personal project, on an unbound legacy session -----------


@pytest.mark.asyncio
async def test_external_personal_adopts_its_legacy_work_anchor_ignoring_a_broken_default(world):
    user = make_user(user_type="external", default_project="Gone", project_names=["Linked"])

    outcome = await gate(user, subscription="personal").resolve_session(None, "Linked", None)

    assert isinstance(outcome, SessionBinding)
    # Personal-paid, on the work project, and an origin that neither guesses a route nor reads as private.
    assert (outcome.project_name, outcome.subscription_type, outcome.project_origin) == (
        "Linked",
        "personal",
        "legacy_project",
    )
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_fresh_candidate_origin_is_never_stamped_on_a_different_legacy_anchor(world):
    user = make_user(user_type="external", default_project="Default")
    evidence = gate(user, subscription="personal", header="Linked", source="profile")

    outcome = await evidence.resolve_session(None, "Default", None)

    assert isinstance(outcome, SessionBinding) and outcome.project_name == "Default"
    assert outcome.project_origin == "legacy_project"
    # Same through the session guard when a caller hands it a candidate for another project.
    candidate = SessionBinding("Linked", "personal", "profile", "folder_not_linked")
    effective, bound = _resolve_session("s", {"owner_email": None, "legacy_project": "Default"}, candidate, 1)
    assert not bound and (effective.project_name, effective.project_origin, effective.route_note) == (
        "Default",
        "legacy_project",
        "",
    )
    shared = SessionBinding("Linked", "corporate", "profile", "folder_not_linked")
    effective, _ = _resolve_session("s", {"owner_email": None, "legacy_project": "Default"}, shared, 1)
    assert (effective.project_name, effective.subscription_type, effective.project_origin, effective.route_note) == (
        "Default",
        "corporate",
        "legacy_project",
        "",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor", ["legacy", "bound"])
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
async def test_external_never_lands_in_an_old_personal_project_for_any_classification(world, anchor, subscription):
    """The no-personal-project policy holds for an old owned personal project too, whatever the classification: not
    a shared-origin personal row, and no write into it."""
    user = make_user(user_type="external", default_project="Default")
    bound, legacy = (SessionBinding(EMAIL, subscription, "personal", ""), None) if anchor == "bound" else (None, EMAIL)

    outcome = await gate(user, subscription=subscription).resolve_session(bound, legacy, None)

    assert outcome == "personal_destination_unavailable"
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_personal_project_is_never_validated_with_a_shared_origin(world):
    private = SessionBinding(EMAIL, "personal", "profile", "")
    assert await gate(subscription="personal").validate_destination(private, False) == "session_project_conflict"
    assert (
        await gate(subscription="personal").validate_destination(SessionBinding(EMAIL, "personal", "personal"), True)
        is None
    )


@pytest.mark.asyncio
async def test_an_ordinary_personal_request_still_cannot_join_a_shared_legacy_anchor(world):
    outcome = await gate(subscription="personal").resolve_session(None, "Linked", None)

    assert outcome == "session_project_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
async def test_external_user_on_a_work_project_anchor_is_never_a_conflict(world, subscription):
    """The private-destination conflict is for an ordinary user only: an external user's Personal-paid session stays
    in its work project, and no other classification is terminated by the policy."""
    user = make_user(user_type="external", default_project="Default")
    outcome = await gate(user, subscription=subscription).resolve_session(LINKED, None, None)

    assert isinstance(outcome, SessionBinding) and outcome.project_name == "Linked"


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["corporate", "codemie", "unknown"])
async def test_a_policy_change_without_a_private_destination_does_not_end_collection(world, subscription):
    """Ordinary user, session bound in the personal project, later classified non-Personal: no conflict, the session
    keeps its binding (no reroute or migration) and is accepted."""
    world.projects[EMAIL].cli_analytics_enabled = False
    personal = SessionBinding(EMAIL, "personal", "personal", "")

    outcome = await gate(subscription=subscription).resolve_session(personal, None, None)

    assert isinstance(outcome, SessionBinding)
    assert (outcome.project_name, outcome.subscription_type, outcome.project_origin) == (EMAIL, "personal", "personal")


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["corporate", "codemie", "unknown"])
async def test_a_non_personal_request_on_a_shared_session_proceeds(world, subscription):
    outcome = await gate(subscription=subscription).resolve_session(LINKED, None, None)

    assert isinstance(outcome, SessionBinding) and outcome.project_name == "Linked"


# --- AC8: every adopted legacy session reads `legacy_project` (review f-69413b32be4f) -----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
async def test_every_classification_adopting_a_legacy_project_reads_legacy_project(world, subscription):
    user = make_user(user_type="external", default_project="Default")  # a Personal request is not private for it

    outcome = await gate(user, subscription=subscription).resolve_session(None, "Linked", None)

    assert isinstance(outcome, SessionBinding)
    assert (outcome.project_name, outcome.subscription_type, outcome.project_origin) == (
        "Linked",
        subscription,
        "legacy_project",
    )


@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
def test_the_session_guard_fallback_reads_legacy_project_for_every_classification(subscription):
    candidate = SessionBinding("Linked", subscription, "profile", "folder_not_linked")

    effective, bound = _resolve_session("s", {"owner_email": None, "legacy_project": "Default"}, candidate, 1)

    assert not bound
    assert (effective.project_name, effective.subscription_type, effective.project_origin, effective.route_note) == (
        "Default",
        subscription,
        "legacy_project",
        "",
    )


@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
def test_an_already_bound_origin_is_never_rewritten_to_legacy_project(subscription):
    stored = {
        "owner_email": "u@example.com",
        "project_name": "Linked",
        "subscription_type": "corporate",
        "project_origin": "linked_folder",
        "route_note": "",
        "legacy_project": "Other",
    }
    candidate = SessionBinding("Default", subscription, "default_project", "folder_not_linked")

    effective, bound = _resolve_session("s", stored, candidate, 1)

    assert bound and (effective.project_name, effective.project_origin) == ("Linked", "linked_folder")


def test_an_ordinary_private_personal_request_still_conflicts_with_a_legacy_project_anchor():
    private = SessionBinding("u@example.com", "personal", "personal", "")

    with pytest.raises(SessionDiscard) as exc:
        _resolve_session("s", {"owner_email": None, "legacy_project": "Linked"}, private, 1)

    assert exc.value.reason == "session_project_conflict"


def test_legacy_project_is_a_shared_vocabulary_origin_so_it_never_reads_as_private():
    assert normalise_project_origin("legacy_project") == "legacy_project"
    assert "legacy_project" in SHARED_ORIGINS


# --- Legacy request on a bound session: the bound destination is validated (review f-c22d973e1400) ---------


def legacy_gate(user=None, header="Linked"):
    return build_ingest_gate(user or make_user(default_project="Default"), header)


def bound_to(project, subscription="corporate", origin="linked_folder"):
    return SessionBinding(project, subscription, origin, "")


@pytest.mark.asyncio
async def test_the_legacy_gate_validates_a_bound_destination_like_the_evidence_gate(world):
    legacy = legacy_gate()
    assert await legacy.validate_destination(bound_to("Linked"), True) is None

    world.projects["linked"].cli_analytics_enabled = False
    assert await legacy.validate_destination(bound_to("Linked"), True) == "cli_analytics_disabled"

    world.projects["linked"].cli_analytics_enabled = True
    world.membership.return_value = None  # live membership revoked
    assert await legacy.validate_destination(bound_to("Linked"), True) == "session_project_unavailable"

    world.membership.return_value = object()
    world.remove("Linked")
    assert await legacy.validate_destination(bound_to("Linked"), True) == "session_project_unavailable"


@pytest.mark.asyncio
async def test_the_legacy_gate_retries_a_failed_lookup_and_discards_an_excluded_personal_destination(world):
    with patch(f"{MODULE}.application_repository.aget_active_by_name_case_insensitive", side_effect=RuntimeError("db")):
        with pytest.raises(TelemetryStorageUnavailableError):
            await legacy_gate().validate_destination(bound_to("Linked"), True)

    external = make_user(user_type="external", default_project="Default")
    personal = bound_to(EMAIL, "personal", "personal")
    assert await legacy_gate(external).validate_destination(personal, True) == "personal_destination_unavailable"
    assert await legacy_gate().validate_destination(personal, True) is None  # the owner keeps their own project


def test_the_legacy_gate_is_not_an_evidence_gate():
    """A validator alone must not make a request an evidence request: no resolver, no binding."""
    legacy = legacy_gate()
    assert callable(legacy.validate_destination)
    assert not hasattr(legacy, "resolve_session") and not hasattr(legacy, "binding")
    # What the ingestor reads from a gate: a validator only, so the request stays legacy and binds nothing.
    assert gate_validator(legacy) is not None
    assert gate_resolver(legacy) is None and gate_binding(legacy) is None


@pytest.mark.parametrize("legacy", ["Legacy", "legacy", "LEGACY"])
@pytest.mark.parametrize("subscription", ["personal", "corporate", "codemie", "unknown"])
def test_a_candidate_naming_the_legacy_project_is_adopted_with_the_legacy_origin_too(subscription, legacy):
    """A matching candidate (any case) does not prove how the legacy rows were routed: origin and note are dropped."""
    candidate = SessionBinding("Legacy", subscription, "profile", "folder_not_linked")

    effective, bound = _resolve_session("s", {"owner_email": None, "legacy_project": legacy}, candidate, 1)

    assert not bound
    assert (effective.project_name, effective.subscription_type, effective.project_origin, effective.route_note) == (
        legacy,
        subscription,
        "legacy_project",
        "",
    )


@pytest.mark.parametrize("legacy", ["u@example.com", "U@Example.com"])
def test_a_private_personal_candidate_on_its_own_matching_legacy_anchor_stays_private(legacy):
    private = SessionBinding("u@example.com", "personal", "personal", "")

    effective, bound = _resolve_session("s", {"owner_email": None, "legacy_project": legacy}, private, 1)

    assert not bound and (effective.project_name, effective.project_origin) == ("u@example.com", "personal")
    assert "personal" not in SHARED_ORIGINS


def test_a_private_personal_candidate_against_another_legacy_project_is_still_a_conflict():
    private = SessionBinding("u@example.com", "personal", "personal", "")

    with pytest.raises(SessionDiscard) as exc:
        _resolve_session("s", {"owner_email": None, "legacy_project": "Linked"}, private, 1)

    assert exc.value.reason == "session_project_conflict"


# --- EPMCDME-16849 item 2: `private_classes` read by every privacy consumer ---
N0 = {"contract_version": "1", "work_consent": None}
N0_CONSENT = {"contract_version": "1", "work_consent": "explicit-folder"}


@pytest.mark.asyncio
async def test_n0_unknown_for_a_regular_user_lands_in_the_personal_project_stamped_with_the_contract(world):
    outcome = await gate(subscription="unknown", header="Linked", source="profile", **N0).resolve_session(
        None, None, None
    )

    assert (outcome.project_name, outcome.project_origin, outcome.subscription_type) == (EMAIL, "personal", "unknown")
    assert (outcome.contract_version, outcome.work_consent) == ("1", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor", ["bound", "legacy"])
async def test_n0_unknown_on_a_session_anchored_elsewhere_is_a_conflict_never_adoption(world, anchor):
    bound, legacy = (dataclasses.replace(LINKED, contract_version="1"), None) if anchor == "bound" else (None, "Linked")

    outcome = await gate(subscription="unknown", **N0).resolve_session(bound, legacy, None)

    assert outcome == "session_project_conflict"
    world.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_with_consent_is_the_consent_gate_and_never_adopts_a_legacy_project(world):
    routed = await gate(subscription="unknown", header="Linked", source="profile", **N0_CONSENT).resolve_session(
        None, None, None
    )
    adopted = await gate(subscription="unknown", header="Linked", **N0_CONSENT).resolve_session(None, "Linked", None)

    assert (routed.project_name, routed.project_origin) == ("Linked", "linked_folder")
    assert (adopted.project_name, adopted.project_origin) == ("Linked", "linked_folder")


@pytest.mark.asyncio
async def test_n0_unknown_never_validates_a_personal_project_with_a_shared_origin(world):
    stamped = SessionBinding(EMAIL, "unknown", "linked_folder", "", contract_version="1")

    assert await gate(subscription="unknown", **N0).validate_destination(stamped, True) == "session_project_conflict"
    consented = SessionBinding(
        EMAIL, "unknown", "linked_folder", "", contract_version="1", work_consent="explicit-folder"
    )
    assert await gate(subscription="unknown", **N0_CONSENT).validate_destination(consented, True) is None


@pytest.mark.asyncio
async def test_legacy_unknown_is_unchanged_by_the_contract_predicate(world):
    adopted = await gate(subscription="unknown").resolve_session(None, "Linked", None)
    bound = await gate(subscription="unknown", header="Linked", source="profile").resolve_session(LINKED, None, None)

    assert (adopted.project_name, adopted.project_origin, adopted.contract_version) == (
        "Linked",
        "legacy_project",
        None,
    )
    assert bound.project_name == "Linked"


def test_private_personal_reads_the_binding_contract():
    own = {"project_name": "u@example.com", "project_origin": "personal", "route_note": ""}
    assert private_personal(SessionBinding(subscription_type="unknown", **own)) is False
    assert private_personal(SessionBinding(subscription_type="unknown", contract_version="1", **own)) is True
    assert private_personal(SessionBinding(subscription_type="personal", **own)) is True
    consent = SessionBinding(subscription_type="personal", contract_version="1", work_consent="explicit-folder", **own)
    assert private_personal(consent) is False


# --- EPMCDME-16849 item 5: (contract, consent) compared before anything else ---
N0_LINKED = SessionBinding("Linked", "corporate", "linked_folder", "", contract_version="1")
CONSENT_LINKED = SessionBinding(
    "Linked", "corporate", "linked_folder", "", contract_version="1", work_consent="explicit-folder"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bound, request_contract",
    [
        (LINKED, N0),  # legacy-bound, contract request
        (N0_LINKED, {}),  # contract-bound, legacy request
        (N0_LINKED, N0_CONSENT),  # personally bound, consent request
        (CONSENT_LINKED, N0),  # consent-bound, plain contract request
    ],
)
async def test_a_contract_or_consent_mismatch_with_the_bound_session_is_a_final_discard(world, bound, request_contract):
    outcome = await gate(header="Linked", source="profile", **request_contract).resolve_session(bound, None, None)

    assert outcome == "session_contract_conflict"


@pytest.mark.asyncio
async def test_a_matching_contract_and_consent_on_a_bound_session_proceeds_and_funding_is_not_compared(world):
    same_n0 = await gate(**N0).resolve_session(N0_LINKED, None, None)
    funded = await gate(**N0).resolve_session(
        SessionBinding("Linked", "corporate", "linked_folder", "", contract_version="1", funding_route="personal"),
        None,
        None,
    )

    assert same_n0.project_name == funded.project_name == "Linked"


@pytest.mark.asyncio
async def test_legacy_l1_on_a_legacy_bound_session_is_unchanged(world):
    outcome = await gate(header="Linked", source="profile").resolve_session(LINKED, None, None)

    assert isinstance(outcome, SessionBinding) and outcome.project_name == "Linked"
    assert (outcome.contract_version, outcome.work_consent) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", ["codemie", "corporate"])
async def test_n0_codemie_and_corporate_resolve_by_todays_route_not_against_the_header_project(world, subscription):
    """The header is a route hint here (a profile source), never compared to a stored project."""
    outcome = await gate(subscription=subscription, header="Linked", source="profile", **N0).resolve_session(
        None, None, None
    )
    defaulted = await gate(subscription=subscription, **N0).resolve_session(None, None, None)

    assert (outcome.project_name, outcome.project_origin) == ("Linked", "profile")
    assert (defaulted.project_name, defaulted.project_origin) == ("Default", "default_project")
    assert (outcome.contract_version, outcome.work_consent) == ("1", None)
