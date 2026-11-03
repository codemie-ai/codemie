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

The defect these guard is silent by construction: under OIDC and EntraID `username == email`, so a
resolver keyed on either one passes every test written against that data and under-reports only on a
Keycloak deployment.
"""

import asyncio
from dataclasses import dataclass
from unittest.mock import patch

from codemie.service.spend.coverage import SpendDimension, SpendResult
from codemie.service.spend.identity_bridge import (
    ResolvedIdentity,
    identity_bridge_portion,
    resolve_identity,
)

USER = "aaaaaaaa-1111-1111-1111-111111111111"


@dataclass
class _Record:
    id: str
    email: str
    username: str
    name: str | None = None


def resolving_to(found: dict):
    return patch(
        "codemie.repository.user_repository.user_repository.afind_users_by_identifiers",
        return_value=found,
    )


class TestAnyOfTheThreeIdentifiersIsAccepted:
    """Callers hold different ones: a budget assignment the id, a decoded end_user a name or email."""

    def test_the_alias_set_holds_username_and_email(self):
        record = _Record(id=USER, email="alice@example.com", username="alice")
        for handed_in in ("alice", "alice@example.com", USER):
            with resolving_to({handed_in: record}):
                resolved = asyncio.run(resolve_identity(object(), handed_in))
            assert resolved.user_id == USER
            assert resolved.aliases == frozenset({"alice", "alice@example.com"})

    def test_identical_username_and_email_de_duplicate(self):
        """Under OIDC/EntraID the two coincide; a two-member set would repeat one literal."""
        record = _Record(id=USER, email="alice@example.com", username="alice@example.com")
        with resolving_to({"alice@example.com": record}):
            resolved = asyncio.run(resolve_identity(object(), "alice@example.com"))
        assert resolved.aliases == frozenset({"alice@example.com"})

    def test_the_display_name_is_never_an_alias(self):
        """The alias set needs the username itself; a display name is not one of the two grammars."""
        record = _Record(id=USER, email="alice@example.com", username="alice", name="Alice Anderson")
        with resolving_to({"alice": record}):
            resolved = asyncio.run(resolve_identity(object(), "alice"))
        assert "Alice Anderson" not in (resolved.aliases or frozenset())


class TestUnresolvableIsAStateAndNotAnEmptySet:
    def test_an_unmatched_principal_yields_none_not_an_empty_frozenset(self):
        """An empty set claims the person has no fallback identities; None says we cannot tell."""
        with resolving_to({}):
            resolved = asyncio.run(resolve_identity(object(), "nobody@example.com"))
        assert resolved.aliases is None
        assert resolved.user_id is None
        assert not resolved.resolved

    def test_an_empty_identifier_asks_nothing(self):
        with resolving_to({}) as lookup:
            resolved = asyncio.run(resolve_identity(object(), ""))
        assert resolved.aliases is None
        lookup.assert_not_called()

    def test_a_resolved_person_with_both_aliases_reports_resolved(self):
        record = _Record(id=USER, email="alice@example.com", username="alice")
        with resolving_to({"alice": record}):
            assert asyncio.run(resolve_identity(object(), "alice")).resolved


class TestThePortionForAnUnresolvableBridge:
    def test_both_members_are_unknown(self):
        """The one portion that is not computable: the rows cannot be IDENTIFIED, so none is in hand."""
        portion = identity_bridge_portion()
        assert portion.spend is None
        assert portion.rows is None

    def test_it_carries_no_sub_range(self):
        """Only a PUBLISHED floor scopes a portion as a time interval; this hole is scattered rows."""
        assert identity_bridge_portion().sub_range is None

    def test_the_result_type_accepts_it_only_on_this_dimension(self):
        """Every other portion holds its rows, so an unknown one elsewhere is a defect, not a state."""
        from decimal import Decimal

        SpendResult(figure=Decimal("1"), partial={SpendDimension.IDENTITY_BRIDGE: identity_bridge_portion()})

        import pytest

        with pytest.raises(ValueError):
            SpendResult(figure=Decimal("1"), partial={SpendDimension.WORKFLOW: identity_bridge_portion()})


class TestTheCollisionCheckCanActuallyFail:
    """AD-26 says enforce, do not assume — so the check must be capable of failing."""

    def test_it_reports_an_alias_claimed_by_two_people(self):
        from codemie.service.spend.identity_bridge import assert_aliases_do_not_collide

        class _Session:
            def __init__(self):
                self.calls = 0

            async def execute(self, _stmt):
                self.calls += 1
                if self.calls == 1:
                    return _Rows([("shared@example.com", 2)])
                return _Scalars(["user-a", "user-b"])

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Scalars:
            def __init__(self, values):
                self._values = values

            def scalars(self):
                return self._values

        collisions = asyncio.run(assert_aliases_do_not_collide(_Session()))
        assert len(collisions) == 1
        assert collisions[0].alias == "shared@example.com"
        assert collisions[0].user_ids == ("user-a", "user-b")

    def test_a_healthy_directory_reports_no_collisions(self):
        from codemie.service.spend.identity_bridge import assert_aliases_do_not_collide

        class _Session:
            async def execute(self, _stmt):
                class _Empty:
                    def all(self):
                        return []

                return _Empty()

        assert asyncio.run(assert_aliases_do_not_collide(_Session())) == []


class TestTheResolvedIdentityShape:
    def test_it_is_immutable(self):
        import pytest

        resolved = ResolvedIdentity(user_id=USER, aliases=frozenset({"alice"}))
        with pytest.raises((AttributeError, TypeError)):
            resolved.user_id = "other"
