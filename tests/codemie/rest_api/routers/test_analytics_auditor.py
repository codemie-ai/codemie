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

"""Tests for auditor access to analytics guards (EPMCDME-10930)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.configs import config
from codemie.rest_api.security.user import User


def _make_auditor():
    with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
        return User(
            id="aud-1",
            email="aud@example.com",
            username="aud",
            is_admin=False,
            is_maintainer=False,
            is_auditor=True,
            admin_project_names=[],
        )


class TestAnalyticsAuditorGuards:
    def test_authorize_admin_budget_view_allows_auditor(self):
        """Auditor caller should not raise in _authorize_admin_budget_view."""
        from codemie.rest_api.routers.analytics import _authorize_admin_budget_view

        auditor = _make_auditor()
        # Should not raise (returns None)
        result = _authorize_admin_budget_view(auditor, {"some-project"})
        assert result is None

    def test_authorize_admin_budget_view_still_raises_for_regular_user(self):
        """Regular user without admin_project_names raises in _authorize_admin_budget_view."""
        from codemie.rest_api.routers.analytics import _authorize_admin_budget_view
        from codemie.core.exceptions import ExtendedHTTPException

        with patch.object(config, 'ENV', 'dev'), patch.object(config, 'ENABLE_USER_MANAGEMENT', True):
            regular = User(
                id="reg-1",
                username="reg",
                is_admin=False,
                is_maintainer=False,
                is_auditor=False,
                admin_project_names=[],
            )
        with pytest.raises(ExtendedHTTPException):
            _authorize_admin_budget_view(regular, {"some-project"})


class TestAnalyticsAuditorCrossProjectAccess:
    """EPMCDME-10930 spec 5.2: auditor must reach the service call (no 403), even for
    a project the auditor has no explicit membership in.
    """

    @pytest.mark.asyncio
    @patch("codemie.rest_api.routers.analytics.AnalyticsService")
    async def test_leaderboard_user_detail_allows_auditor(self, mock_service_class):
        from codemie.rest_api.routers.analytics import get_leaderboard_user_detail

        mock_service = AsyncMock()
        mock_service.get_leaderboard_user_detail.return_value = {}
        mock_service_class.return_value = mock_service

        with patch("codemie.rest_api.routers.analytics._create_response", return_value=MagicMock()):
            await get_leaderboard_user_detail(user_id="target-1", user=_make_auditor())

        mock_service.get_leaderboard_user_detail.assert_called_once()
