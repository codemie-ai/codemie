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


"""Single decision point for which budget pays for a request: target -> default project -> personal (EPMCDME-16625).

Pure logic: callers supply the user's memberships and an ``is_funded`` check (active budget + allocation for the
user). Exhaustion is deliberately not considered here: an exhausted budget is still the chosen one and is never
rerouted (hard limit blocks, soft limit warns, both enforced downstream).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from codemie.configs import config, logger
from codemie.rest_api.security.user import derive_idp_default_project, personal_workspace_names, same_project_name


class AssetKind(StrEnum):
    PROJECT_ASSET = "project_asset"  # assistant / workflow / datasource that belongs to a project
    NO_ASSET = "no_asset"  # skill run, generation, workflow execution without an asset
    CLI = "cli"  # CLI / IDE proxy request, optionally naming a project


class FallbackKind(StrEnum):
    DEFAULT_PROJECT = "default_project"
    PERSONAL = "personal"


@dataclass(frozen=True, slots=True)
class AttributionInput:
    kind: AssetKind
    asset_project: str | None
    is_global: bool
    email: str
    member_projects: frozenset[str]  # member and admin projects
    default_project: str | None
    cli_project: str | None = None
    # Other names of the user's own personal budget besides the email (IDP-claims username workspace).
    personal_projects: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AttributionDecision:
    charged_project: str | None  # None means the personal budget
    targeted_project: str | None  # where the request was first aimed (the user's default when no asset project)
    fallback_kind: FallbackKind | None  # None when the first step paid
    ignored_cli_project: str | None = None
    # Projects of the target -> default walk, in order, without the personal budget. The runtime walks them again
    # with the model it actually builds, so a category the probe did not cover still reaches the default (AR-1).
    candidates: tuple[str, ...] = ()
    # A funded check failed, so a later step may have paid only because funding could not be read (AR-1).
    funding_unknown: bool = False


def _names(value: object) -> list[str]:
    """Membership lists as plain names; anything else (partial user objects) counts as no memberships."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return [name for name in value if isinstance(name, str)]
    return []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def member_projects(user: object) -> frozenset[str]:
    """Projects the user may charge: member and admin projects."""
    return frozenset(_names(getattr(user, "project_names", None)) + _names(getattr(user, "admin_project_names", None)))


def member_spelling(project: str | None, members: frozenset[str]) -> str | None:
    """The user's own spelling of a member project, or None when they are not a member.

    An exact name wins; otherwise a single case-insensitive match maps a typed variant (CLI header) to the
    member project as budgets store it. Two members differing only in case are ambiguous and match nothing.
    """
    if not project:
        return None
    if project in members:
        return project
    variants = [member for member in members if same_project_name(member, project)]
    return variants[0] if len(variants) == 1 else None


def _idp_default_project(project_names: list[str], email: str, username: object) -> str | None:
    """IDP-claims mode stores no default, so attribution derives one (D8). It is kept off the user object: the
    credentials lookup reads user.default_project and must not change with billing (EPMCDME-16625 row 18)."""
    if config.ENABLE_USER_MANAGEMENT:
        return None
    return derive_idp_default_project(project_names, email or None, _text(username))


def attribution_input_for_user(
    user: object,
    *,
    kind: AssetKind,
    asset_project: str | None = None,
    is_global: bool = False,
    cli_project: str | None = None,
    fallback_default: str | None = None,
) -> AttributionInput:
    """Build the resolver input from a user, tolerating partial user objects (internal callers, test doubles)."""
    project_names = _names(getattr(user, "project_names", None))
    email = _text(getattr(user, "email", None)) or ""
    members = member_projects(user)
    personal = personal_workspace_names(email, _text(getattr(user, "username", None)), project_names)
    # A caller's project hint stands in for a missing default only when the user may charge it (CR-007).
    if (
        fallback_default
        and fallback_default not in members
        and not any(same_project_name(fallback_default, name) for name in personal)
    ):
        fallback_default = None
    return AttributionInput(
        kind=kind,
        asset_project=asset_project,
        is_global=is_global,
        email=email,
        member_projects=members,
        default_project=_text(getattr(user, "default_project", None))
        or _idp_default_project(project_names, email, getattr(user, "username", None))
        or fallback_default,
        cli_project=cli_project,
        personal_projects=personal,
    )


def _is_personal(project: str | None, inp: AttributionInput) -> bool:
    return same_project_name(project, inp.email) or any(same_project_name(project, p) for p in inp.personal_projects)


def _is_member(project: str | None, inp: AttributionInput) -> bool:
    # Exact: projects differing only in case are different projects, and budgets are keyed by exact name.
    return bool(project) and project in inp.member_projects


def select_target(inp: AttributionInput) -> tuple[str | None, str | None]:
    """Return ``(target, ignored_cli_project)``; a ``None`` target means the user's default project."""
    if inp.kind is AssetKind.CLI:
        if not inp.cli_project:
            return None, None
        if spelling := member_spelling(inp.cli_project, inp.member_projects):
            return spelling, None
        return None, inp.cli_project
    if inp.kind is AssetKind.NO_ASSET or inp.is_global or not inp.asset_project:
        return None, None
    if _is_personal(inp.asset_project, inp):
        return None, None
    if _is_member(inp.asset_project, inp):
        return inp.asset_project, None
    return None, None


def _candidates(target: str | None, inp: AttributionInput) -> list[str]:
    # Exact names: projects differing only in case are different budgets, so a case-variant default is still
    # its own step (AR-6).
    ordered: list[str] = []
    for name in (target, inp.default_project):
        if name and name not in ordered:
            ordered.append(name)
    return ordered


def payable_candidates(candidates: list[str], inp: AttributionInput) -> tuple[str, ...]:
    """The project steps of the walk: everything before the first personal step."""
    payable: list[str] = []
    for name in candidates:
        if _is_personal(name, inp):
            break
        payable.append(name)
    return tuple(payable)


@dataclass(frozen=True, slots=True)
class ChargeOutcome:
    """Where a walk ended up: the step it was aimed at first, and how the charge fell back from it."""

    targeted_project: str | None
    fallback_kind: FallbackKind | None

    @property
    def fallback_from(self) -> str | None:
        return self.targeted_project if self.fallback_kind else None


def charge_outcome(steps: Sequence[str], paying_step: str | None) -> ChargeOutcome:
    """The outcome when ``paying_step`` paid on a walk over ``steps`` (its project steps, personal excluded).

    ``None`` is the personal budget. The one place that names an outcome, for the decision, model construction and
    the CLI runtime walk alike (final review AR-2): the first step paying is no fallback; any later project step is
    the default project, the walk's only other project step; the personal budget after a project step is personal.
    """
    targeted = steps[0] if steps else None
    if paying_step is None:
        return ChargeOutcome(targeted, FallbackKind.PERSONAL if targeted else None)
    if targeted is None or paying_step == targeted:
        return ChargeOutcome(targeted, None)
    return ChargeOutcome(targeted, FallbackKind.DEFAULT_PROJECT)


def _decision(
    inp: AttributionInput,
    ignored: str | None,
    charged_index: int | None,
    candidates: list[str],
    *,
    funding_unknown: bool = False,
) -> AttributionDecision:
    targeted = candidates[0] if candidates else None
    payable = payable_candidates(candidates, inp)
    charged = candidates[charged_index] if charged_index is not None else None
    kind = charge_outcome(payable, charged).fallback_kind
    return AttributionDecision(charged, targeted, kind, ignored, payable, funding_unknown)


class FundingCheckAborted(Exception):
    """Raised by an is_funded check to stop the walk: the request cannot be decided now and fails at once.

    Other failures count as unknown funding and the walk moves on; this one is for failures that would only repeat
    on every later step, such as a saturated connection pool (EPMCDME-16625 final review AR-6).
    """


def _log_probe_failure(project: str) -> None:
    logger.warning(f"budget_event=funded_probe_failed component=attribution_resolver project_name={project!r}")


def resolve_attribution(inp: AttributionInput, is_funded: Callable[[str], bool]) -> AttributionDecision:
    """Walk target -> default project -> personal and charge the first funded step.

    A failing funded check counts as unfunded, on web and CLI alike: the walk moves on and the decision says
    funding was unknown, so the charge is logged as such rather than as an ordinary fallback (AR-1).
    """
    target, ignored = select_target(inp)
    candidates = _candidates(target, inp)
    funding_unknown = False
    for index, name in enumerate(candidates):
        if _is_personal(name, inp):
            break  # the personal project is the personal budget: nothing left to probe
        try:
            funded = is_funded(name)
        except FundingCheckAborted:
            raise
        except Exception:
            _log_probe_failure(name)
            funding_unknown = True
            funded = False
        if funded:
            return _decision(inp, ignored, index, candidates, funding_unknown=funding_unknown)
    return _decision(inp, ignored, None, candidates, funding_unknown=funding_unknown)


async def aresolve_attribution(
    inp: AttributionInput, is_funded: Callable[[str], Awaitable[bool]]
) -> AttributionDecision:
    """Async twin of :func:`resolve_attribution`."""
    target, ignored = select_target(inp)
    candidates = _candidates(target, inp)
    funding_unknown = False
    for index, name in enumerate(candidates):
        if _is_personal(name, inp):
            break
        try:
            funded = await is_funded(name)
        except FundingCheckAborted:
            raise
        except Exception:
            _log_probe_failure(name)
            funding_unknown = True
            funded = False
        if funded:
            return _decision(inp, ignored, index, candidates, funding_unknown=funding_unknown)
    return _decision(inp, ignored, None, candidates, funding_unknown=funding_unknown)
