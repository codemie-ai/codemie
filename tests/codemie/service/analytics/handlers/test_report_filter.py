# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""`report_filter_for`: the request's identifiers become the port's filter, none of them dropped."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.spend.ledger import ReportFilter, UserTerm

MODULE = "codemie.service.analytics.handlers.report_filter"


def _record(user_id: str, username: str, email: str):
    return SimpleNamespace(id=user_id, username=username, email=email)


def _resolving(records: dict):
    """Patch the session and the bulk lookup so the test controls which identifiers resolve."""
    session = patch(f"{MODULE}.get_async_session")
    lookup = patch(f"{MODULE}.user_repository")
    return session, lookup, records


class TestTheUserTerms:
    @pytest.mark.asyncio
    async def test_a_resolved_identifier_carries_the_persons_canonical_id_and_alias_set(self):
        session, lookup, records = _resolving({"u1": _record("u1", "alice", "alice@example.com")})
        with session as ctx, lookup as repo:
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            repo.afind_users_by_identifiers = AsyncMock(return_value=records)

            result = await report_filter_for(["u1"], None)

        assert result.users == (UserTerm(user_id="u1", aliases=frozenset({"alice", "alice@example.com"})),)

    @pytest.mark.asyncio
    async def test_an_unresolved_identifier_stays_a_term_with_no_alias_set(self):
        """Dropped, the money would widen to everyone while the rows stayed narrowed; `None`, never
        an empty set, is what the port labels as a bridge hole."""
        session, lookup, records = _resolving({})
        with session as ctx, lookup as repo:
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            repo.afind_users_by_identifiers = AsyncMock(return_value=records)

            result = await report_filter_for(["ghost"], None)

        assert result.users == (UserTerm(user_id="ghost", aliases=None),)

    @pytest.mark.asyncio
    async def test_the_lookup_is_one_bulk_query_over_the_deduplicated_set_in_request_order(self):
        session, lookup, records = _resolving({"u1": _record("u1", "alice", "alice@example.com")})
        with session as ctx, lookup as repo:
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            repo.afind_users_by_identifiers = AsyncMock(return_value=records)

            result = await report_filter_for(["u2", "u1", "u2", ""], None)

        repo.afind_users_by_identifiers.assert_awaited_once()
        assert repo.afind_users_by_identifiers.await_args.args[1] == {"u1", "u2"}
        assert [term.user_id for term in result.users] == ["u2", "u1"]


class TestALookupFailureDegradesTheFilter:
    @pytest.mark.asyncio
    async def test_every_term_stays_and_carries_no_alias_set_and_the_failure_is_logged_and_counted(self):
        """The dashboards this feeds needed only Elasticsearch before; a database outage in this one
        round trip must not become their error. The terms survive, unbridged, so the ledger labels
        the figure rather than widening it."""
        session, lookup, _ = _resolving({})
        with (
            session as ctx,
            lookup as repo,
            patch(f"{MODULE}.logger") as log,
            patch(f"{MODULE}.BaseMonitoringService") as monitoring,
            _owners(set()),
        ):
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            repo.afind_users_by_identifiers = AsyncMock(side_effect=RuntimeError("postgres down"))

            result = await report_filter_for(["u1", "u2"], ["P"])

        assert result.users == (UserTerm(user_id="u1", aliases=None), UserTerm(user_id="u2", aliases=None))
        assert result.projects == ("P",)
        log.warning.assert_called_once()
        assert "postgres down" in log.warning.call_args.args[0]
        monitoring.send_count_metric.assert_called_once()
        assert monitoring.send_count_metric.call_args.kwargs["attributes"] == {
            "ledger": "identity_bridge",
            "read": "report_filter",
        }


class TestTheEmptyStates:
    @pytest.mark.asyncio
    async def test_no_identifiers_means_no_lookup_and_the_whole_window(self):
        with patch(f"{MODULE}.get_async_session") as ctx, patch(f"{MODULE}.user_repository") as repo:
            result = await report_filter_for(None, None)

        assert result == ReportFilter()
        assert result.is_empty
        ctx.assert_not_called()
        repo.afind_users_by_identifiers.assert_not_called()

    @pytest.mark.asyncio
    async def test_projects_pass_through_deduplicated_and_ask_only_which_are_personal(self):
        with (
            patch(f"{MODULE}.get_async_session") as ctx,
            patch(f"{MODULE}.user_repository") as users,
            _owners(set()) as applications,
        ):
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await report_filter_for([], ["P", "Q", "P", ""])

        assert result.projects == ("P", "Q")
        assert result.users == ()
        assert result.personal_project_owners == ()
        assert applications.aget_personal_project_owners.await_args.args[1] == ["P", "Q"]
        users.afind_users_by_identifiers.assert_not_called()


def _owners(owners: set):
    """Patch the personal-project lookup: `owners` are the owner ids of the personal projects among the names."""
    return patch(f"{MODULE}.application_repository", aget_personal_project_owners=AsyncMock(return_value=owners))


class TestAPersonalProjectIsMatchedThroughItsOwner:
    """UD-H-D2 (pass H): a personal project's calls reach the ledger in principal form with no project,
    so the port can match them only through the project's owner - resolved here, beside the user terms."""

    @pytest.mark.asyncio
    async def test_a_personal_project_carries_its_owners_id_and_no_user_lookup(self):
        """The owner's pot is keyed on `users.id` alone, so there is no alias set to look up."""
        with (
            patch(f"{MODULE}.get_async_session") as ctx,
            patch(f"{MODULE}.user_repository") as users,
            _owners({"u1"}) as applications,
        ):
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await report_filter_for(None, ["atlas-core", "me@corp.com"])

        assert result.projects == ("atlas-core", "me@corp.com")
        assert result.personal_project_owners == ("u1",)
        assert applications.aget_personal_project_owners.await_args.args[1] == ["atlas-core", "me@corp.com"]
        users.afind_users_by_identifiers.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_personal_project_with_no_recorded_owner_leaves_the_owners_unknown(self):
        """No owner id means no pot to key on; `None` makes the port label the figure rather than read the
        project's name alone as its whole spend."""
        with patch(f"{MODULE}.get_async_session") as ctx, patch(f"{MODULE}.user_repository"), _owners({None, "u1"}):
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await report_filter_for(None, ["orphan@corp.com", "me@corp.com"])

        assert result.personal_project_owners is None

    @pytest.mark.asyncio
    async def test_a_failed_lookup_leaves_the_owners_unknown_and_is_logged_and_counted(self):
        """The same degradation as a failed user lookup: the request survives, the figure is labelled."""
        failing = patch(
            f"{MODULE}.application_repository",
            aget_personal_project_owners=AsyncMock(side_effect=RuntimeError("postgres down")),
        )
        with (
            patch(f"{MODULE}.get_async_session") as ctx,
            failing,
            patch(f"{MODULE}.logger") as log,
            patch(f"{MODULE}.BaseMonitoringService") as monitoring,
        ):
            ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await report_filter_for(None, ["P"])

        assert result.projects == ("P",)
        assert result.personal_project_owners is None
        log.warning.assert_called_once()
        assert "postgres down" in log.warning.call_args.args[0]
        assert monitoring.send_count_metric.call_args.kwargs["attributes"] == {
            "ledger": "identity_bridge",
            "read": "report_filter",
        }
