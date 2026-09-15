# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from codemie.service.budget.budget_notification_service import (
    _MAX_DEDUP_WINDOW,
    _effective_dedup_window,
    notify_soft_limit_reached,
)


def _budget(**overrides):
    defaults = {
        "budget_id": "b1",
        "name": "B1",
        "soft_budget": 80.0,
        "max_budget": 100.0,
        "budget_duration": "30d",
        "notification_owner_email": "owner@example.com",
        "soft_limit_notified_at": None,
        "soft_limit_notify_once": False,
        "soft_limit_notification_enabled": True,
        "project_name": None,
        "budget_category": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patch_session(session):
    """Patch get_async_session to yield the given session via async context manager."""
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    return patch(
        "codemie.service.budget.budget_notification_service.get_async_session",
        return_value=ctx,
    )


# ---------------------------------------------------------------------------
# Dedup window clamping (CR-005)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("60s", timedelta(seconds=60)),
        ("30m", timedelta(minutes=30)),
        ("12h", timedelta(hours=12)),
        ("24h", timedelta(hours=24)),
        # Anything longer than 24h clamps to 24h.
        ("25h", timedelta(hours=24)),
        ("7d", timedelta(hours=24)),
        ("30d", timedelta(hours=24)),
    ],
)
def test_effective_dedup_window_clamps_at_24h(raw, expected):
    assert _effective_dedup_window(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "abc", "30", "1x"])
def test_effective_dedup_window_returns_none_for_invalid(raw):
    assert _effective_dedup_window(raw) is None


def test_max_dedup_window_is_24_hours():
    assert timedelta(hours=24) == _MAX_DEDUP_WINDOW


# ---------------------------------------------------------------------------
# notify_soft_limit_reached — happy paths + skips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_owner_email_is_none():
    session = AsyncMock()
    budget = _budget(notification_owner_email=None)
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock()
        email.send_budget_soft_limit_notification = AsyncMock()

        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        repo.try_claim_soft_limit_notification.assert_not_awaited()
        email.send_budget_soft_limit_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_owner_email_is_whitespace_only():
    session = AsyncMock()
    budget = _budget(notification_owner_email="   ")
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock()
        email.send_budget_soft_limit_notification = AsyncMock()

        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        repo.try_claim_soft_limit_notification.assert_not_awaited()
        email.send_budget_soft_limit_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_budget_duration_is_invalid():
    """CR-009: an invalid stored duration must not bypass dedup and spam emails."""
    session = AsyncMock()
    budget = _budget(budget_duration="not-a-duration")
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock()
        email.send_budget_soft_limit_notification = AsyncMock()

        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        repo.try_claim_soft_limit_notification.assert_not_awaited()
        email.send_budget_soft_limit_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_atomic_claim_returns_false():
    """CR-002: when another caller already claimed the slot, do NOT send an email."""
    session = AsyncMock()
    budget = _budget()
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock(return_value=False)
        email.send_budget_soft_limit_notification = AsyncMock()

        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        repo.try_claim_soft_limit_notification.assert_awaited_once()
        email.send_budget_soft_limit_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_notifies_when_atomic_claim_succeeds():
    """CR-002/CR-008: reserve first, commit, THEN send email."""
    session = AsyncMock()
    budget = _budget(budget_duration="30d")
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock(return_value=True)
        email.send_budget_soft_limit_notification = AsyncMock(return_value=True)

        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        repo.try_claim_soft_limit_notification.assert_awaited_once()
        # Dedup window passed to the repo must be the CLAMPED window (≤24h).
        window = repo.try_claim_soft_limit_notification.await_args.kwargs["window"]
        assert window == timedelta(hours=24)
        # Commit happens BEFORE the email is sent (reserve-first pattern).
        session.commit.assert_awaited_once()
        email.send_budget_soft_limit_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_reads_budget_attributes_before_commit_expires_them():
    """Regression: session.commit() expires ORM attributes.

    SQLAlchemy's ``expire_on_commit`` defaults to True, so any attribute read
    after the commit triggers a lazy refresh that raises ``MissingGreenlet``
    under asyncio. The notifier must snapshot name/id/duration BEFORE the
    commit. This stub mimics that expiry so the ordering is enforced.
    """

    class ExpiringBudget:
        """Raises on attribute access once the session has committed."""

        def __init__(self):
            self._expired = False
            self._values = {
                "budget_id": "b1",
                "name": "B1",
                "budget_duration": "30d",
                "notification_owner_email": "owner@example.com",
                "soft_limit_notified_at": None,
                "soft_limit_notify_once": False,
                "soft_limit_notification_enabled": True,
                "project_name": None,
                "budget_category": None,
            }

        def expire(self):
            self._expired = True

        def __getattr__(self, item):
            values = object.__getattribute__(self, "_values")
            if item not in values:
                raise AttributeError(item)
            if object.__getattribute__(self, "_expired"):
                raise RuntimeError("greenlet_spawn has not been called; can't call await_only() here")
            return values[item]

    budget = ExpiringBudget()
    session = AsyncMock()
    session.commit = AsyncMock(side_effect=budget.expire)

    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock(return_value=True)
        email.send_budget_soft_limit_notification = AsyncMock(return_value=True)

        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

    # The email must still go out with the pre-commit snapshot values.
    email.send_budget_soft_limit_notification.assert_awaited_once()
    kwargs = email.send_budget_soft_limit_notification.await_args.kwargs
    assert kwargs["budget_name"] == "B1"
    assert kwargs["budget_id"] == "b1"
    assert kwargs["email"] == "owner@example.com"


@pytest.mark.asyncio
async def test_notify_passes_clamped_window_for_short_duration():
    """CR-005: a short budget_duration (60s) still uses the parsed value, not clamped down."""
    session = AsyncMock()
    budget = _budget(budget_duration="60s")
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock(return_value=True)
        email.send_budget_soft_limit_notification = AsyncMock(return_value=True)

        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        window = repo.try_claim_soft_limit_notification.await_args.kwargs["window"]
        assert window == timedelta(seconds=60)


# ---------------------------------------------------------------------------
# Fail-open (all failure modes must be swallowed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_open_when_email_service_raises():
    """SMTP failure must not raise. Slot is already claimed → dedup will hold until window passes."""
    session = AsyncMock()
    budget = _budget()
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock(return_value=True)
        email.send_budget_soft_limit_notification = AsyncMock(side_effect=RuntimeError("smtp down"))

        # Must not raise.
        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)


@pytest.mark.asyncio
async def test_fail_open_when_budget_missing():
    session = AsyncMock()
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=None)
        repo.try_claim_soft_limit_notification = AsyncMock()
        email.send_budget_soft_limit_notification = AsyncMock()

        await notify_soft_limit_reached(budget_id="ghost", current_spend=85.0, soft_limit=80.0)

        repo.try_claim_soft_limit_notification.assert_not_awaited()
        email.send_budget_soft_limit_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_open_when_db_lookup_raises():
    with (
        patch("codemie.service.budget.budget_notification_service.get_async_session") as gas,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        gas.side_effect = RuntimeError("db down")
        email.send_budget_soft_limit_notification = AsyncMock()

        # Must not raise.
        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        email.send_budget_soft_limit_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_open_when_atomic_claim_raises():
    session = AsyncMock()
    budget = _budget()
    with (
        _patch_session(session),
        patch("codemie.service.budget.budget_notification_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_notification_service.email_service") as email,
    ):
        repo.get_by_id = AsyncMock(return_value=budget)
        repo.try_claim_soft_limit_notification = AsyncMock(side_effect=RuntimeError("db down"))
        email.send_budget_soft_limit_notification = AsyncMock()

        # Must not raise.
        await notify_soft_limit_reached(budget_id="b1", current_spend=85.0, soft_limit=80.0)

        email.send_budget_soft_limit_notification.assert_not_awaited()
