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

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.activity_event import ActivityEventListItem, ActivityEventFilterOptions
from codemie.service.activity.activity_event_service import ActivityEventService
from codemie.service.activity.activity_repository import ActivityEventRow


def _row(**kwargs) -> ActivityEventRow:
    defaults = {
        "id": "evt-1",
        "domain": "user_management",
        "event_type": "user.created",
        "entity_type": "user",
        "entity_id": "u-1",
        "actor_id": "a-1",
        "actor_email": "admin@test.com",
        "actor_name": "Admin",
        "attributes": None,
        "created_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return ActivityEventRow(**defaults)


class TestListEvents:
    @patch("codemie.service.activity.activity_event_service.activity_event_repository")
    @patch("codemie.service.activity.activity_event_service.Session")
    @patch("codemie.service.activity.activity_event_service.PostgresClient")
    def test_returns_paginated_items_and_total(self, mock_pg, mock_session_cls, mock_repo):
        mock_repo.find_all.return_value = ([_row()], 1)
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        svc = ActivityEventService()
        items, total = svc.list_events(limit=10, offset=0)

        assert total == 1
        assert len(items) == 1
        assert isinstance(items[0], ActivityEventListItem)
        assert items[0].id == "evt-1"
        assert items[0].actor_email == "admin@test.com"

    @patch("codemie.service.activity.activity_event_service.activity_event_repository")
    @patch("codemie.service.activity.activity_event_service.Session")
    @patch("codemie.service.activity.activity_event_service.PostgresClient")
    def test_passes_all_filters_to_repository(self, mock_pg, mock_session_cls, mock_repo):
        mock_repo.find_all.return_value = ([], 0)
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        from_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 12, 31, tzinfo=timezone.utc)

        svc = ActivityEventService()
        svc.list_events(
            actor_id="a-1",
            domain="user_management",
            event_type="user.created",
            entity_type="user",
            entity_id="u-1",
            from_dt=from_dt,
            to_dt=to_dt,
            sort_dir="asc",
            limit=25,
            offset=50,
        )

        call_kwargs = mock_repo.find_all.call_args.kwargs
        assert call_kwargs["actor_id"] == "a-1"
        assert call_kwargs["domain"] == "user_management"
        assert call_kwargs["sort_dir"] == "asc"
        assert call_kwargs["limit"] == 25
        assert call_kwargs["offset"] == 50

    @patch("codemie.service.activity.activity_event_service.activity_event_repository")
    @patch("codemie.service.activity.activity_event_service.Session")
    @patch("codemie.service.activity.activity_event_service.PostgresClient")
    def test_returns_empty_list_when_no_events(self, mock_pg, mock_session_cls, mock_repo):
        mock_repo.find_all.return_value = ([], 0)
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        svc = ActivityEventService()
        items, total = svc.list_events(limit=10, offset=0)

        assert items == []
        assert total == 0


class TestGetFilterOptions:
    @patch("codemie.service.activity.activity_event_service.activity_event_repository")
    @patch("codemie.service.activity.activity_event_service.Session")
    @patch("codemie.service.activity.activity_event_service.PostgresClient")
    def test_returns_distinct_values_from_repository(self, mock_pg, mock_session_cls, mock_repo):
        mock_repo.get_distinct_domains.return_value = ["budget_management", "user_management"]
        mock_repo.get_distinct_event_types.return_value = ["budget.created", "user.created"]
        mock_repo.get_distinct_entity_types.return_value = ["budget", "user"]
        mock_repo.get_domain_mapping.return_value = {}
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        svc = ActivityEventService()
        opts = svc.get_filter_options()

        assert isinstance(opts, ActivityEventFilterOptions)
        assert opts.domains == ["budget_management", "user_management"]
        assert opts.event_types == ["budget.created", "user.created"]
        assert opts.entity_types == ["budget", "user"]

    @patch("codemie.service.activity.activity_event_service.activity_event_repository")
    @patch("codemie.service.activity.activity_event_service.Session")
    @patch("codemie.service.activity.activity_event_service.PostgresClient")
    def test_mapping_included_in_response(self, mock_pg, mock_session_cls, mock_repo):
        from codemie.rest_api.models.activity_event import DomainFilterEntry

        mock_repo.get_distinct_domains.return_value = ['budget_management']
        mock_repo.get_distinct_event_types.return_value = ['budget.created']
        mock_repo.get_distinct_entity_types.return_value = ['budget']
        mock_repo.get_domain_mapping.return_value = {
            'budget_management': DomainFilterEntry(event_types=['budget.created'], entity_types=['budget'])
        }
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        opts = ActivityEventService().get_filter_options()

        assert 'budget_management' in opts.mapping
        assert opts.mapping['budget_management'].event_types == ['budget.created']

    @patch("codemie.service.activity.activity_event_service.activity_event_repository")
    @patch("codemie.service.activity.activity_event_service.Session")
    @patch("codemie.service.activity.activity_event_service.PostgresClient")
    def test_returns_empty_lists_when_table_has_no_events(self, mock_pg, mock_session_cls, mock_repo):
        mock_repo.get_distinct_domains.return_value = []
        mock_repo.get_distinct_event_types.return_value = []
        mock_repo.get_distinct_entity_types.return_value = []
        mock_repo.get_domain_mapping.return_value = {}
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        svc = ActivityEventService()
        opts = svc.get_filter_options()

        assert opts.domains == []
        assert opts.event_types == []
        assert opts.entity_types == []
