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


"""Matrix tests for the attribution resolver (EPMCDME-16625)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codemie.service.budget.attribution_resolver import (
    AssetKind,
    AttributionInput,
    FallbackKind,
    aresolve_attribution,
    resolve_attribution,
    select_target,
)

EMAIL = "u@example.com"


def _inp(
    kind: AssetKind = AssetKind.PROJECT_ASSET,
    asset_project: str | None = "team-a",
    is_global: bool = False,
    members: tuple[str, ...] = ("team-a", "team-d", EMAIL),
    default: str | None = "team-d",
    cli_project: str | None = None,
    email: str = EMAIL,
) -> AttributionInput:
    return AttributionInput(
        kind=kind,
        asset_project=asset_project,
        is_global=is_global,
        email=email,
        member_projects=frozenset(members),
        default_project=default,
        cli_project=cli_project,
    )


def _run(inp: AttributionInput, funded: set[str]):
    probed: list[str] = []

    def is_funded(project: str) -> bool:
        probed.append(project)
        return project in funded

    return resolve_attribution(inp, is_funded), probed


# (id, input, funded projects, charged_project, fallback_kind, targeted_project)
MATRIX = [
    ("1-personal-asset-team-default", _inp(asset_project=EMAIL), {"team-d"}, "team-d", None, "team-d"),
    (
        "1b-personal-asset-default-unfunded",
        _inp(asset_project=EMAIL),
        set(),
        None,
        FallbackKind.PERSONAL,
        "team-d",
    ),
    ("2-personal-asset-no-assigned-default", _inp(asset_project=EMAIL, default=EMAIL), set(), None, None, EMAIL),
    ("3-member-funded", _inp(), {"team-a", "team-d"}, "team-a", None, "team-a"),
    ("4-member-unfunded-default-funded", _inp(), {"team-d"}, "team-d", FallbackKind.DEFAULT_PROJECT, "team-a"),
    ("4b-member-unfunded-default-unfunded", _inp(), set(), None, FallbackKind.PERSONAL, "team-a"),
    ("5-shared-nonmember", _inp(members=("team-d", EMAIL)), {"team-a", "team-d"}, "team-d", None, "team-d"),
    ("6-global-member", _inp(is_global=True), {"team-a", "team-d"}, "team-d", None, "team-d"),
    (
        "7-global-nonmember-default-unfunded",
        _inp(is_global=True, members=("team-d",)),
        set(),
        None,
        FallbackKind.PERSONAL,
        "team-d",
    ),
    (
        "8-global-asset-in-own-personal-project",
        _inp(is_global=True, asset_project=EMAIL),
        {"team-d"},
        "team-d",
        None,
        "team-d",
    ),
    ("12-no-asset", _inp(kind=AssetKind.NO_ASSET, asset_project=None), {"team-d"}, "team-d", None, "team-d"),
    ("14-cli-no-project", _inp(kind=AssetKind.CLI, asset_project=None), {"team-d"}, "team-d", None, "team-d"),
    (
        "15-cli-member-project-funded",
        _inp(kind=AssetKind.CLI, asset_project=None, cli_project="team-a"),
        {"team-a", "team-d"},
        "team-a",
        None,
        "team-a",
    ),
    (
        "15b-cli-member-project-unfunded",
        _inp(kind=AssetKind.CLI, asset_project=None, cli_project="team-a"),
        {"team-d"},
        "team-d",
        FallbackKind.DEFAULT_PROJECT,
        "team-a",
    ),
    (
        "16-cli-nonmember-project",
        _inp(kind=AssetKind.CLI, asset_project=None, cli_project="other", members=("team-d",)),
        {"team-d", "other"},
        "team-d",
        None,
        "team-d",
    ),
    ("same-project-unfunded", _inp(asset_project="team-d"), set(), None, FallbackKind.PERSONAL, "team-d"),
]


@pytest.mark.parametrize(
    "inp,funded,charged,kind,targeted",
    [pytest.param(*row[1:], id=row[0]) for row in MATRIX],
)
def test_matrix_row(inp, funded, charged, kind, targeted):
    decision, _ = _run(inp, funded)

    assert decision.charged_project == charged
    assert decision.fallback_kind == kind
    assert decision.targeted_project == targeted


def test_shared_nonmember_never_probes_asset_project():
    _, probed = _run(_inp(members=("team-d", EMAIL)), {"team-a", "team-d"})

    assert "team-a" not in probed


def test_global_member_never_probes_asset_project():
    _, probed = _run(_inp(is_global=True), {"team-a", "team-d"})

    assert "team-a" not in probed


def test_cli_nonmember_project_is_ignored_and_reported():
    inp = _inp(kind=AssetKind.CLI, asset_project=None, cli_project="other", members=("team-d",))

    decision, probed = _run(inp, {"team-d", "other"})

    assert decision.ignored_cli_project == "other"
    assert "other" not in probed
    assert select_target(inp) == (None, "other")


def test_asset_membership_is_exact():
    """CR-008: 'Team-A' is a different project from 'team-a'; a case variant never grants membership."""
    decision, probed = _run(_inp(asset_project="Team-A", members=("team-a", "team-d")), {"Team-A", "team-d"})

    assert decision.charged_project == "team-d"
    assert "Team-A" not in probed


def test_cli_case_variant_header_maps_to_member_spelling():
    inp = _inp(kind=AssetKind.CLI, asset_project=None, cli_project="TEAM-A")

    decision, _ = _run(inp, {"team-a"})

    assert decision.charged_project == "team-a"
    assert decision.ignored_cli_project is None


def test_non_member_fallback_hint_is_not_a_default():
    """CR-007: a caller-supplied project hint is used as the default only for its members."""
    from types import SimpleNamespace

    from codemie.service.budget.attribution_resolver import attribution_input_for_user

    user = SimpleNamespace(email=EMAIL, username="u", project_names=[], admin_project_names=[], default_project=None)

    assert attribution_input_for_user(user, kind=AssetKind.NO_ASSET, fallback_default="foreign").default_project is None
    member = SimpleNamespace(**{**vars(user), "project_names": ["team-a"]})
    assert (
        attribution_input_for_user(member, kind=AssetKind.NO_ASSET, fallback_default="team-a").default_project
        == "team-a"
    )


def test_probe_error_counts_as_unfunded():
    def is_funded(project: str) -> bool:
        raise RuntimeError("db down")

    decision = resolve_attribution(_inp(), is_funded)

    assert decision.charged_project is None
    assert decision.fallback_kind == FallbackKind.PERSONAL
    assert decision.funding_unknown is True


def test_known_funding_is_not_reported_as_unknown():
    decision, _ = _run(_inp(), set())

    assert decision.funding_unknown is False


def test_case_variant_default_is_its_own_step():
    """AR-6: 'Team-A' and 'team-a' are different budgets, so an unfunded target never hides a funded default."""
    decision, probed = _run(_inp(asset_project="team-a", members=("team-a", "Team-A"), default="Team-A"), {"Team-A"})

    assert probed == ["team-a", "Team-A"]
    assert decision.charged_project == "Team-A"
    assert decision.fallback_kind == FallbackKind.DEFAULT_PROJECT


def test_decision_carries_the_project_steps_for_the_runtime():
    """AR-1: model construction walks the same steps again, so the decision hands them over without personal."""
    decision, _ = _run(_inp(), {"team-a"})
    assert decision.candidates == ("team-a", "team-d")

    personal_default, _ = _run(_inp(default=EMAIL), set())
    assert personal_default.candidates == ("team-a",)


def test_no_email_user_does_not_raise():
    decision, _ = _run(_inp(email="", members=("team-a",), default=None), set())

    assert decision.charged_project is None


@pytest.mark.asyncio
@pytest.mark.parametrize("inp,funded", [pytest.param(row[1], row[2], id=row[0]) for row in MATRIX])
async def test_async_matches_sync(inp, funded):
    is_funded = AsyncMock(side_effect=lambda project: project in funded)

    expected, _ = _run(inp, funded)

    assert await aresolve_attribution(inp, is_funded) == expected


def test_idp_username_workspace_counts_as_personal():
    inp = AttributionInput(
        kind=AssetKind.NO_ASSET,
        asset_project=None,
        is_global=False,
        email=EMAIL,
        member_projects=frozenset({"carol", "team-a"}),
        default_project="carol",
        personal_projects=frozenset({"carol"}),
    )

    decision, probed = _run(inp, {"carol"})

    assert decision.charged_project is None
    assert decision.fallback_kind is None
    assert probed == []


def test_asset_in_idp_username_workspace_targets_default():
    inp = AttributionInput(
        kind=AssetKind.PROJECT_ASSET,
        asset_project="carol",
        is_global=False,
        email=EMAIL,
        member_projects=frozenset({"carol", "team-d"}),
        default_project="team-d",
        personal_projects=frozenset({"carol"}),
    )

    decision, _ = _run(inp, {"team-d"})

    assert decision.charged_project == "team-d"


def test_an_aborted_funded_check_stops_the_walk():
    """Final review AR-6: a failure that would repeat on every step is not counted as unknown funding."""
    from codemie.service.budget.attribution_resolver import FundingCheckAborted

    probed = []

    def is_funded(project):
        probed.append(project)
        raise FundingCheckAborted("pool exhausted")

    inp = _abort_input()
    with pytest.raises(FundingCheckAborted):
        resolve_attribution(inp, is_funded)
    assert probed == ["team-a"]


@pytest.mark.asyncio
async def test_an_aborted_async_funded_check_stops_the_walk():
    from codemie.service.budget.attribution_resolver import FundingCheckAborted

    async def is_funded(project):
        raise FundingCheckAborted("pool exhausted")

    with pytest.raises(FundingCheckAborted):
        await aresolve_attribution(_abort_input(), is_funded)


def _abort_input():
    from codemie.service.budget.attribution_resolver import AssetKind, AttributionInput

    return AttributionInput(
        kind=AssetKind.PROJECT_ASSET,
        asset_project="team-a",
        is_global=False,
        email="carol@example.com",
        member_projects=frozenset({"team-a", "team-d"}),
        default_project="team-d",
    )


@pytest.mark.parametrize(
    "steps,paying,expected_from,expected_kind",
    [
        (["team-a", "team-d"], "team-a", None, None),
        (["team-a", "team-d"], "team-d", "team-a", "default_project"),
        (["team-a", "team-d"], None, "team-a", "personal"),
        (["team-d"], "team-d", None, None),
        ([], None, None, None),
    ],
    ids=["target-pays", "default-pays", "personal-pays", "asset-less-default-pays", "personal-only"],
)
def test_charge_outcome_names_every_outcome_in_one_place(steps, paying, expected_from, expected_kind):
    """Final review AR-2: the decision, model construction and the CLI runtime walk share this rule."""
    from codemie.service.budget.attribution_resolver import charge_outcome

    outcome = charge_outcome(steps, paying)

    assert outcome.fallback_from == expected_from
    assert (outcome.fallback_kind.value if outcome.fallback_kind else None) == expected_kind
