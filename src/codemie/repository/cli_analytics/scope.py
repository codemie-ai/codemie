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

"""Who may see which CLI-analytics session: the viewer scope and its truth table.

Row classes: P = a session_dims row private to its owner (project_origin 'personal', or subscription_type
'personal' with an origin that is not a shared one; a Personal-paid session routed to a work project, shared
origin, is N and follows the project's read permissions, and its cost counts like any other: `sql_personal_billed` names
the billing class);
N = a session_dims row that is not P; M = no session_dims row (legacy). A missing
owner never turns a row into Personal and never matches a viewer.

    exclude   N and authNP
    own_only  P and owned
    detail    (P and owned) or ((N or M) and authNP)

owned = the viewer is the row's owner, or the scope `sees_private` (set for an admin, maintainer and auditor, by
product decision EPMCDME-17657: they read every private Personal row in own_only and detail; exclude never lists
Personal).

authNP: unrestricted, or a non-empty project_name in admin_projects, or `own_shared` and the viewer is the row's
owner (a regular user reads their own shared sessions, never anyone else's). Fails closed: an absent or
malformed scope, or an unknown mode / row class, allows nothing.
`allowed` is the reference; the SQL builders express the same table as parameterised conditions.
"""

from dataclasses import dataclass
from typing import Any, Literal

Mode = Literal["exclude", "own_only", "detail"]
RowClass = Literal["P", "N", "M"]
_MODES = ("exclude", "own_only", "detail")
DENY = "false"


# One trim set for every Python and SQL normalisation here: ASCII whitespace. SQL btrim defaults to spaces only
# and Python strip() to all Unicode whitespace; both sides use exactly this set so a tab or newline cannot match
# in one and not the other. The set is exactly: space, tab, LF, CR, form feed, vertical tab. PostgreSQL escape
# strings have no \v (an unknown escape is the literal letter), so the vertical tab is written as octal \013.
_WS = " \t\n\r\f\v"
_SQL_WS = r"E' \t\n\r\f\013'"


def sql_norm(expr: str) -> str:
    """lower(btrim(coalesce(expr, ''), whitespace)): the SQL twin of `_py_norm`."""
    return f"lower(btrim(coalesce({expr}, ''), {_SQL_WS}))"


def _py_norm(value: str | None) -> str:
    return (value or "").strip(_WS).lower()


def normalise_email(email: str | None) -> str | None:
    """lower(trim(email)) over `_WS`, None when missing or blank."""
    return _py_norm(email) or None


@dataclass(frozen=True)
class ViewerScope:
    viewer_email: str | None
    unrestricted: bool
    admin_projects: frozenset[str]
    mode: Mode
    sees_private: bool = False
    own_shared: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "viewer_email", normalise_email(self.viewer_email))


def _valid(scope: Any) -> bool:
    return (
        isinstance(scope, ViewerScope)
        and scope.mode in _MODES
        and isinstance(scope.unrestricted, bool)
        and isinstance(scope.admin_projects, frozenset)
        and isinstance(scope.sees_private, bool)
        and isinstance(scope.own_shared, bool)
    )


def allowed(scope: Any, row_class: RowClass, owner_email: str | None, project_name: str | None) -> bool:
    if not _valid(scope) or row_class not in ("P", "N", "M"):
        return False
    owner = normalise_email(owner_email)
    is_owner = scope.viewer_email is not None and owner == scope.viewer_email
    owned = row_class == "P" and (scope.sees_private or is_owner)
    project = project_name or ""
    auth_np = (
        scope.unrestricted or (project != "" and project in scope.admin_projects) or (scope.own_shared and is_owner)
    )
    if scope.mode == "exclude":
        return row_class == "N" and auth_np
    if scope.mode == "own_only":
        return owned
    return owned or (row_class != "P" and auth_np)


def sql_private(a: str) -> str:
    """Class P over the columns of alias `a`: the owner-only scope. Not the billing class: a Personal-paid session
    routed to a work project (shared origin, external and service-account users) is N and follows the project's
    read permissions. Fails closed: Personal-paid with a missing or unknown origin stays private."""
    subscription = sql_norm(f"{a}.subscription_type")
    origin = sql_norm(f"{a}.project_origin")
    shared = ", ".join(f"'{o}'" for o in SHARED_ORIGINS)
    return f"({origin} = 'personal' OR ({subscription} = 'personal' AND {origin} NOT IN ({shared})))"


FUNDING_ROUTES = ("api_based", "subscription", "unknown")


def sql_personal_billed(a: str) -> str:
    """The billing class over alias `a`: paid by the person, whichever project it is stored under."""
    return f"({sql_norm(f'{a}.subscription_type')} = 'personal')"


def _owned_sql(scope: ViewerScope, a: str, params: dict[str, Any]) -> str:
    """Class P rows the viewer may read: every one for `sees_private`, else the viewer's own."""
    is_p = sql_private(a)
    if scope.sees_private:
        return is_p
    if not scope.viewer_email:
        return DENY
    owner = sql_norm(f"{a}.owner_email")
    params["scope_viewer"] = scope.viewer_email
    return f"({is_p} AND {owner} <> '' AND {owner} = $scope_viewer::text)"


def _non_personal_sql(scope: ViewerScope, a: str, params: dict[str, Any]) -> str:
    """Non-private rows the viewer may read: all (unrestricted), those of the admin projects, and the viewer's own
    (`own_shared`); DENY when none applies."""
    if scope.unrestricted:
        auth_np = "true"
    else:
        clauses = []
        projects = sorted(p for p in scope.admin_projects if p)
        if projects:
            params["scope_projects"] = projects
            clauses.append(f"coalesce({a}.project_name, '') = ANY($scope_projects::text[])")
        if scope.own_shared and scope.viewer_email:
            owner = sql_norm(f"{a}.owner_email")
            params["scope_viewer"] = scope.viewer_email
            clauses.append(f"({owner} <> '' AND {owner} = $scope_viewer::text)")
        if not clauses:
            return DENY
        auth_np = f"({' OR '.join(clauses)})"
    return f"(NOT {sql_private(a)} AND {auth_np})"


def _condition(scope: Any, a: str) -> tuple[str, dict[str, Any]]:
    """The truth table over the columns of alias `a` (owner_email, project_name, subscription_type,
    project_origin); a LEFT-JOINed alias with no row has NULL columns, which makes it class M."""
    if not _valid(scope):
        return DENY, {}
    params: dict[str, Any] = {}
    owned = _owned_sql(scope, a, params)
    non_personal = _non_personal_sql(scope, a, params)

    if scope.mode == "own_only":
        sql = owned
    elif scope.mode == "exclude":
        sql = non_personal
    else:
        sql = " OR ".join(c for c in (owned, non_personal) if c != DENY) or DENY
        sql = f"({sql})" if sql != DENY else sql
    # exclude/own_only are list modes: x is always a session_dims row, so M cannot reach them.
    return (DENY, {}) if sql == DENY else (sql, params)


def sql_list_condition(scope: Any) -> tuple[str, dict[str, Any]]:
    """Condition over the projected `x` of `_sessions_cte` (exclude / own_only for lists)."""
    return _condition(scope, "x")


def sql_detail_condition(scope: Any) -> tuple[str, dict[str, Any]]:
    """Condition over `session_dims d` LEFT JOINed by session_id: no row = class M."""
    return _condition(scope, "d")


# --- Response vocabulary of subscription_type / project_origin (shared by filters and responses) ---
# The values the ingest side writes (cli_classification.classify, cli_analytics_project_gate ORIGIN_*
# and PROJECT_SOURCES); kept here because the repository layer must not import the service layer.
SUBSCRIPTION_TYPES = ("codemie", "corporate", "personal", "unknown")
# Overview splits the cost in two: API is the sessions routed through the CodeMie proxy, Subscription is every other
# class (Personal-paid included).
API_SUBSCRIPTION_TYPE = "codemie"
# `legacy_project`: a session (any classification) that adopted the project of its earlier (legacy) rows; the real
# routing origin is unknown, so none is guessed, but the row must not read as missing (= private for Personal-paid).
ORIGIN_LEGACY_PROJECT = "legacy_project"
PROJECT_ORIGINS = (
    "personal",
    "linked_folder",
    "profile",
    "registered_repository",
    "default_project",
    ORIGIN_LEGACY_PROJECT,
)
SHARED_ORIGINS = tuple(o for o in PROJECT_ORIGINS if o != "personal")


def private_classes(contract_version: str | None, work_consent: str | None) -> frozenset[str]:
    """The subscription classes whose destination is private (owner-only), decided once for every consumer.
    Legacy (no contract): personal. Contract 1 without work consent: personal and unknown (an unclassified
    session is not disclosed on an organization list). Contract 1 with consent: none."""
    if contract_version is None:
        return frozenset({"personal"})
    return frozenset() if work_consent else frozenset({"personal", "unknown"})


def _normalise(value: str | None, known: tuple[str, ...], missing: str) -> str:
    v = _py_norm(value)
    if not v:
        return missing
    return v if v in known else "other"


def normalise_subscription_type(value: str | None) -> str:
    """Stored subscription_type -> response value: known kept, missing/empty `unknown`, else `other`."""
    return _normalise(value, SUBSCRIPTION_TYPES, "unknown")


def normalise_project_origin(value: str | None) -> str:
    """Stored project_origin -> response value: known kept, missing/empty `none`, else `other`."""
    return _normalise(value, PROJECT_ORIGINS, "none")


def _sql_normalised(column: str, known: tuple[str, ...], missing: str) -> str:
    v = sql_norm(column)
    values = ", ".join(f"'{k}'" for k in known)
    return f"(CASE WHEN {v} = '' THEN '{missing}' WHEN {v} IN ({values}) THEN {v} ELSE 'other' END)"


def sql_subscription_type(column: str) -> str:
    """SQL expression equal to `normalise_subscription_type` over a column."""
    return _sql_normalised(column, SUBSCRIPTION_TYPES, "unknown")


def sql_project_origin(column: str) -> str:
    """SQL expression equal to `normalise_project_origin` over a column."""
    return _sql_normalised(column, PROJECT_ORIGINS, "none")
