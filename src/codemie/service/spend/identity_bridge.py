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

"""One person, all the identities the platform can emit for them.

**Why a set and not an identifier.** On rows the fallback parser attributed, a person is *not* one
string: some code paths put `users.username` in scope and some put `users.email`, for the same human
and the same work. Under OIDC and EntraID the two coincide, so a reader keyed on either passes every
test against that data - and under Keycloak, where `username` is `preferred_username`, it silently
under-reports. Embeddings sit on the email path and are 92.4% of rows.

**Scoped to fallback rows only.** A row carrying explicit identity needs none of this: it holds
`users.id`, one canonical value independent of code path. The set is retained because pre-rollout
rows and foreign traffic do not stop existing at activation, and a reader that forgets them
under-reports history.

**Unresolvable is a state, not an error.** Legacy-JWT mode, a deleted user, a principal that matches
nobody - the resolver returns what it *can* cover and says the other grammar is missing. That
annotation is a `partial` entry on the `identity_bridge` dimension and **never** an `unavailable`
flag: the period is fully covered and every row sits at or above the coverage floor, so nothing here
is unavailable in that sense. It is, though, the one portion whose `spend` **and** `rows` are both
unknown, because an unresolvable alias set means the rows cannot be *identified* - so there is no row
set in hand to sum or to count.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import logger
from codemie.service.spend.coverage import Portion, SpendDimension

__all__ = [
    "AliasCollision",
    "ResolvedIdentity",
    "aliases_for_user",
    "assert_aliases_do_not_collide",
    "identity_bridge_portion",
    "resolve_identity",
]


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """A person's canonical id and every alias the composers can emit for them.

    `aliases` is `None` - not empty - when the bridge could not resolve the input at all. The two
    are different facts and a reader must not treat them alike: an empty set would read as "this
    person has no fallback identities", which is a claim, while `None` says "we could not find out".
    """

    user_id: str | None
    aliases: frozenset[str] | None

    @property
    def resolved(self) -> bool:
        return self.aliases is not None


async def resolve_identity(session: AsyncSession, identifier: str) -> ResolvedIdentity:
    """Resolve any one of `users.id`, `users.username` or `users.email` to the whole set.

    **Accepts any of the three because callers hold different ones**: a budget assignment holds the
    id, a decoded `end_user` holds a username or an email, and an analytics filter holds whichever
    the screen collected.

    The existing analytics identity resolver cannot serve this: its output targets are
    `email | name | id` and the `name` target returns a *display* name where one exists, so it
    cannot deterministically produce the username - and the alias set needs the username itself. So
    this is an addition beside it, not a replacement for it.
    """
    from codemie.repository.user_repository import user_repository

    if not identifier:
        return ResolvedIdentity(user_id=None, aliases=None)

    found = await user_repository.afind_users_by_identifiers(session, {identifier})
    record = found.get(identifier)
    if record is None:
        # Not an error: a deleted user, or a principal belonging to no CodeMie account. The caller
        # reports the missing portion rather than substituting the other grammar's key.
        logger.debug(f"spend_identity_bridge: no user matches {identifier!r}; alias set is unresolvable")
        return ResolvedIdentity(user_id=None, aliases=None)

    return ResolvedIdentity(user_id=record.id, aliases=aliases_for_user(record.username, record.email))


def aliases_for_user(username: str | None, email: str | None) -> frozenset[str]:
    """The alias set for a person whose record the caller **already holds**.

    Split out of `resolve_identity` because a list screen has just loaded every user row it is about
    to show, and re-resolving each one would be a query per row for facts already in memory. The
    rule stays in one place: a caller assembling the set inline would sooner or later use the email
    alone - the field it happens to have nearest - and under Keycloak, where `username` is
    `preferred_username`, that silently under-reports every person whose rows carry the other one.

    De-duplicated on purpose: under OIDC/EntraID `username == email`, and a two-member set there
    would put the same literal into an `IN` clause twice.
    """
    return frozenset(alias for alias in (username, email) if alias)


def identity_bridge_portion() -> Portion:
    """The annotation for a figure whose fallback half could not be identified.

    Both members are unknown, which is legitimate for this dimension **only**. Every other portion
    holds its rows and is missing merely a key, so a `None` on any other dimension is a defect
    rather than a state. A surface renders *an unknown amount is missing* - never a zero, never a
    blank, and never the row count standing in for the money.
    """
    return Portion(spend=None, rows=None)


IDENTITY_BRIDGE_DIMENSION = SpendDimension.IDENTITY_BRIDGE


@dataclass(frozen=True, slots=True)
class AliasCollision:
    """One alias claimed by two different people."""

    alias: str
    user_ids: tuple[str, ...]


async def assert_aliases_do_not_collide(session: AsyncSession) -> list[AliasCollision]:
    """Prove the username-email union is collision-free. **Enforce, do not assume.**

    `users.username` and `users.email` are each unique **independently**, so nothing in the schema
    stops user A's email from equalling user B's username. An alias set built over a union that does
    collide silently merges two people's spend into one figure - no error, no duplicate, and no
    screen that looks wrong.

    Returns the collisions found, so a caller can fail loudly or report them; an empty list is the
    healthy answer. Kept a query rather than a database constraint because the two columns are
    written at request time from IDP claims CodeMie does not shape-check, so the union cannot be
    constrained without rejecting logins.
    """
    from sqlalchemy import func, select

    from codemie.rest_api.models.user_management import UserDB

    active = (UserDB.is_active.is_(True), UserDB.deleted_at.is_(None))

    # One row per (alias, owner) over the union, then keep the aliases owned by more than one person.
    usernames = select(func.lower(UserDB.username).label("alias"), UserDB.id.label("user_id")).where(*active)
    emails = select(func.lower(UserDB.email).label("alias"), UserDB.id.label("user_id")).where(*active)
    union = usernames.union(emails).subquery()

    rows = (
        await session.execute(
            select(union.c.alias, func.count(func.distinct(union.c.user_id)).label("owners"))
            .group_by(union.c.alias)
            .having(func.count(func.distinct(union.c.user_id)) > 1)
        )
    ).all()
    if not rows:
        return []

    collisions = []
    for alias, _owners in rows:
        owner_ids = (await session.execute(select(union.c.user_id).where(union.c.alias == alias).distinct())).scalars()
        collisions.append(AliasCollision(alias=alias, user_ids=tuple(sorted(owner_ids))))

    logger.error(
        "spend_identity_bridge: the username/email union is NOT collision-free - "
        f"{len(collisions)} alias(es) are claimed by more than one user, so their spend would merge: "
        f"{[collision.alias for collision in collisions]}"
    )
    return collisions
