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

"""GET /overview hands its two optional parts to the handler as asked, and keeps them on unless a caller turns them off
(EPMCDME-17052)."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.rest_api.security.user import User
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

END = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _params() -> router_module.FilterParams:
    return router_module.FilterParams(
        time_period="last_7_days",
        start_date=None,
        end_date=None,
        users=None,
        projects=None,
        repositories=None,
        subscription_type=None,
        project_origin=None,
    )


async def _get_overview(handler: MagicMock, **flags) -> None:
    async def fake_resolve(self, user, project_unattributed=False, personal_only=False):
        return router_module.LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=END - timedelta(days=7), end_dt=END)

    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=handler),
        patch.object(router_module.FilterParams, "resolve", fake_resolve),
        patch.object(router_module, "_metadata", return_value={}),
        patch.object(router_module, "_respond", return_value=MagicMock()),
    ):
        await router_module.get_overview(user=User(id="u1"), filters=_params(), **flags)


def _handler() -> MagicMock:
    handler = MagicMock()
    handler.get_overview = AsyncMock(return_value=({}, MagicMock(unpriced=[], catalog_source="litellm"), None))
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("class_lists", [True, False])
@pytest.mark.parametrize("buckets", [True, False])
async def test_the_router_forwards_both_flags_to_the_handler_as_given(class_lists, buckets):
    handler = _handler()

    await _get_overview(handler, include_class_lists=class_lists, include_buckets=buckets)

    handler.get_overview.assert_awaited_once()
    assert handler.get_overview.await_args.kwargs == {"include_class_lists": class_lists, "include_buckets": buckets}


def test_the_overview_flags_are_on_unless_a_caller_turns_them_off():
    """A request that sends neither gets the answer it got before they existed: the lists and the estimates filled."""
    params = inspect.signature(router_module.get_overview).parameters

    assert params["include_class_lists"].default.default is True
    assert params["include_buckets"].default.default is True
