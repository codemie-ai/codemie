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
from unittest.mock import patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.activity_event import ActivityEventFilterOptions, ActivityEventListItem
from codemie.rest_api.routers.activity_events_router import (
    get_filter_options,
    list_activity_events,
)


def _item() -> ActivityEventListItem:
    return ActivityEventListItem(
        id="evt-1",
        domain="user_management",
        event_type="user.created",
        created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )


class TestListActivityEvents:
    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_returns_paginated_response(self, mock_svc):
        mock_svc.list_events.return_value = ([_item()], 1)

        response = list_activity_events(
            actor_id=None,
            domain=None,
            event_type=None,
            entity_type=None,
            entity_id=None,
            from_=None,
            to=None,
            sort_dir="desc",
            limit=50,
            offset=0,
            _=None,
        )

        assert response.pagination.total == 1
        assert response.pagination.per_page == 50
        assert response.pagination.page == 0
        assert len(response.data) == 1
        assert response.data[0].id == "evt-1"

    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_passes_filters_to_service(self, mock_svc):
        mock_svc.list_events.return_value = ([], 0)

        list_activity_events(
            actor_id="a-1",
            domain="user_management",
            event_type="user.created",
            entity_type="user",
            entity_id="u-1",
            from_=None,
            to=None,
            sort_dir="asc",
            limit=25,
            offset=100,
            _=None,
        )

        call_kwargs = mock_svc.list_events.call_args.kwargs
        assert call_kwargs["actor_id"] == "a-1"
        assert call_kwargs["domain"] == "user_management"
        assert call_kwargs["sort_dir"] == "asc"
        assert call_kwargs["limit"] == 25
        assert call_kwargs["offset"] == 100

    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_wraps_unexpected_exceptions_in_http_500(self, mock_svc):
        mock_svc.list_events.side_effect = RuntimeError("db error")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_activity_events(
                actor_id=None,
                domain=None,
                event_type=None,
                entity_type=None,
                entity_id=None,
                from_=None,
                to=None,
                sort_dir="desc",
                limit=50,
                offset=0,
                _=None,
            )

        assert exc_info.value.code == 500

    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_propagates_extended_http_exceptions(self, mock_svc):
        original = ExtendedHTTPException(code=403, message="forbidden")
        mock_svc.list_events.side_effect = original

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_activity_events(
                actor_id=None,
                domain=None,
                event_type=None,
                entity_type=None,
                entity_id=None,
                from_=None,
                to=None,
                sort_dir="desc",
                limit=50,
                offset=0,
                _=None,
            )

        assert exc_info.value is original

    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_pagination_pages_calculated_correctly(self, mock_svc):
        items = [_item() for _ in range(10)]
        mock_svc.list_events.return_value = (items, 47)

        response = list_activity_events(
            actor_id=None,
            domain=None,
            event_type=None,
            entity_type=None,
            entity_id=None,
            from_=None,
            to=None,
            sort_dir="desc",
            limit=10,
            offset=20,
            _=None,
        )

        assert response.pagination.total == 47
        assert response.pagination.pages == 5  # ceil(47/10)
        assert response.pagination.page == 2  # offset(20) // limit(10)


class TestGetFilterOptions:
    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_returns_filter_options_from_service(self, mock_svc):
        mock_svc.get_filter_options.return_value = ActivityEventFilterOptions(
            domains=["user_management"],
            event_types=["user.created"],
            entity_types=["user"],
        )

        response = get_filter_options(_=None)

        assert response.domains == ["user_management"]
        assert response.event_types == ["user.created"]
        assert response.entity_types == ["user"]

    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_returns_mapping_field(self, mock_svc):
        from codemie.rest_api.models.activity_event import DomainFilterEntry

        mock_svc.get_filter_options.return_value = ActivityEventFilterOptions(
            domains=["budget_management"],
            event_types=["budget.created"],
            entity_types=["budget"],
            mapping={
                "budget_management": DomainFilterEntry(
                    event_types=["budget.created"],
                    entity_types=["budget"],
                )
            },
        )

        result = get_filter_options(_=None)

        assert "budget_management" in result.mapping
        assert result.mapping["budget_management"].event_types == ["budget.created"]
        assert result.mapping["budget_management"].entity_types == ["budget"]

    @patch("codemie.rest_api.routers.activity_events_router.activity_event_service")
    def test_wraps_unexpected_exception_in_http_500(self, mock_svc):
        mock_svc.get_filter_options.side_effect = RuntimeError("db error")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_filter_options(_=None)

        assert exc_info.value.code == 500
