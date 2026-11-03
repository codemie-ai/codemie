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

"""Decomposing `end_user` - the fallback path only - and the two guards that bound it.

**This is the fallback.** CodeMie sends identity explicitly in the same
`x-litellm-spend-logs-metadata` header that carries the correlation ids, and where a row passes
both ingest preconditions the uploader *copies* that identity and does not consult `end_user`
at all. What is parsed here is only what does not land `explicit`: history written before the
identity rollout, traffic that reached LiteLLM without passing through CodeMie, and any
CodeMie row whose tuple failed a precondition.

**Composition is read-only.** `build_user_id` and `_project_customer_id` are untouched; this
module is their inverse and its agreement with them is proven by a round-trip test rather than
assumed. That test is release-blocking.

**The inverse is qualified, because one composer is not injective.**
``build_user_id("robot_codemie_cli", PLATFORM)`` and ``build_user_id("robot", CLI)`` both return
``robot_codemie_cli``, so a parser must resolve that one way and the other input cannot
round-trip. Invertibility is therefore claimed only over domain **I**:

    a principal ends with neither reserved suffix **and** does not begin with `codemie:project:`

The prefix half is the sharper one. A principal beginning `codemie:project:` is composed
verbatim on the platform path with no suffix appended, so the emitted `end_user` is
byte-identical to a genuine project-scoped value and parses as one - booking one person's spend
against **another project's budget**, with both readings well-formed and nothing failing. That
is attribution corruption, not a mislabelled category, and it cannot be recovered downstream:
pre-composition is the last point at which the two are still distinguishable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from codemie.service.budget.budget_enums import BudgetCategory

#: Grammar A's fixed prefix. Also the injection vector obligation 4 names.
PROJECT_PREFIX = "codemie:project:"
_CATEGORY_SEPARATOR = ":category:"
_USER_SEPARATOR = ":user:"

#: `build_user_id` appends exactly one of these, so the parser strips exactly one.
RESERVED_SUFFIXES: tuple[str, ...] = tuple(
    f"_codemie_{category.value}" for category in BudgetCategory if category is not BudgetCategory.PLATFORM
)

#: The shape §4.3 validates Grammar A's trailing user field against.
UUID_RE = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


class AttributionForm(str, Enum):
    """The *shape* of `end_user`, which is not the same question as its provenance."""

    PROJECT_SCOPED = "project_scoped"
    PRINCIPAL = "principal"
    UNATTRIBUTED = "unattributed"


@dataclass(frozen=True, slots=True)
class ParsedAttribution:
    """What one `end_user` value decomposes into.

    A parse failure yields `UNATTRIBUTED` with **no category**. The two outcomes are never
    collapsed: `derive_category_from_user_id` falls back to `PLATFORM` for anything
    unrecognised, which silently converts a parse failure into someone else's spend, and this
    parser deliberately does not inherit that.
    """

    form: AttributionForm
    project: str | None = None
    category: BudgetCategory | None = None
    user_id: str | None = None
    principal: str | None = None
    channel: str | None = None

    @property
    def is_attributed(self) -> bool:
        return self.form is not AttributionForm.UNATTRIBUTED


UNATTRIBUTED = ParsedAttribution(form=AttributionForm.UNATTRIBUTED)


def parse_end_user(end_user: str | None) -> ParsedAttribution:
    """Decompose an `end_user` value, or report it unattributed.

    Grammar A is tried first because its prefix is unambiguous; anything else is Grammar B,
    which accepts almost any non-empty string.
    """
    if not end_user:
        return UNATTRIBUTED
    if end_user.startswith(PROJECT_PREFIX):
        return _parse_project_scoped(end_user)
    return _parse_principal(end_user)


def _parse_project_scoped(end_user: str) -> ParsedAttribution:
    """Grammar A: ``codemie:project:<project>:category:<category>:user:<user_id>``, right-anchored.

    Parsed from the **right**: the last `:user:`, then the last `:category:` preceding it. Both
    trailing fields are drawn from closed, colon-free vocabularies - the category from
    `BudgetCategory`, the user id a UUID - so the final separators are unambiguous and the
    remainder is the project **verbatim**.

    That makes it an exact inverse for *every* project identifier, including one containing a
    literal `:category:` or `:user:`, which left-anchored non-greedy matching silently
    mis-splits. Either trailing field failing validation rejects the value **as a whole**: the
    project field is never read and no principal reading is attempted.
    """
    user_at = end_user.rfind(_USER_SEPARATOR)
    if user_at == -1:
        return UNATTRIBUTED
    user_id = end_user[user_at + len(_USER_SEPARATOR) :]
    if not UUID_RE.match(user_id):
        return UNATTRIBUTED

    head = end_user[:user_at]
    category_at = head.rfind(_CATEGORY_SEPARATOR)
    if category_at == -1:
        return UNATTRIBUTED
    try:
        category = BudgetCategory(head[category_at + len(_CATEGORY_SEPARATOR) :])
    except ValueError:
        return UNATTRIBUTED

    project = head[len(PROJECT_PREFIX) : category_at]
    if not project:
        return UNATTRIBUTED

    return ParsedAttribution(
        form=AttributionForm.PROJECT_SCOPED,
        project=project,
        category=category,
        user_id=user_id,
    )


def _parse_principal(end_user: str) -> ParsedAttribution:
    """Grammar B: ``<principal>[_codemie_<channel>]``.

    Strips **exactly one** trailing reserved suffix, never repeatedly, mirroring
    `build_user_id`, which appends exactly one: ``x_codemie_cli_codemie_cli`` yields principal
    ``x_codemie_cli``, not ``x``.

    A value that parsed cleanly and carried no suffix is `platform` - which is precisely what
    `build_user_id` encoded, and is not the forbidden fallback. The forbidden one is assigning
    a category to a value that *failed* to parse.
    """
    for suffix in RESERVED_SUFFIXES:
        if end_user.endswith(suffix) and len(end_user) > len(suffix):
            return ParsedAttribution(
                form=AttributionForm.PRINCIPAL,
                principal=end_user[: -len(suffix)],
                category=BudgetCategory(suffix.removeprefix("_codemie_")),
                channel=suffix.lstrip("_"),
            )
    return ParsedAttribution(
        form=AttributionForm.PRINCIPAL,
        principal=end_user,
        category=BudgetCategory.PLATFORM,
        channel=None,
    )


# ---------------------------------------------------------------------------
# Domain I and the pre-composition guard
# ---------------------------------------------------------------------------


def is_in_domain_i(raw_principal: str) -> bool:
    """Is this principal one the round-trip is claimed over?

    Tests the **raw** value directly, with **no strip**. Obligation 1's strip governs the
    *parser*, which reads a value a suffix has already been appended to; here nothing has been
    appended, so a strip removes a genuine part of the username and readmits the exact
    collision this domain excludes - ``robot_codemie_cli`` strips to ``robot``, which ends in
    no reserved suffix and would pass, yet the two compositions still collide.

    Checking only the suffix half enforces strictly less than **I** and leaves the prefix half
    unguarded, and that half cannot be recovered downstream.
    """
    if raw_principal.startswith(PROJECT_PREFIX):
        return False
    return not any(raw_principal.endswith(suffix) for suffix in RESERVED_SUFFIXES)


#: What a rejected principal is composed from instead.
#:
#: The guarantee is a mechanism, not a convention. A bare stand-in string would parse as an
#: ordinary *principal* under Grammar B - which accepts almost anything - landing
#: `attr_form = 'principal'` with an unresolvable owner. So the sentinel begins
#: `codemie:project:`, routing it to Grammar A, with trailing `:category:` and `:user:` fields
#: deliberately outside the closed vocabularies: Grammar A rejects it **as a whole**, its
#: project field is never read, and the row lands unattributed at ingest - a state reached
#: deliberately rather than inherited.
UNATTRIBUTED_SENTINEL = f"{PROJECT_PREFIX}__rejected__:category:__none__:user:__none__"


def safe_principal(raw_principal: str) -> str:
    """The value a call site emits as `end_user`, substituting the sentinel where needed.

    **This does not fail the request.** Refusing service over the shape of a name would be a
    new failure mode; the discipline wherever an identity is missing is to proceed and surface
    the absence, never to deny. The caller is expected to emit a metric naming the rejected
    principal alongside this call.

    Nothing is lost where explicit identity is present: the uploader never parses `end_user`
    there, so the row lands `explicit` with the caller's true identity and the sentinel
    neutralises nothing. That is the better outcome, not a gap.
    """
    return raw_principal if is_in_domain_i(raw_principal) else UNATTRIBUTED_SENTINEL
