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

from datetime import datetime, timezone
from codemie.rest_api.models.activity_event import (
    ActivityEventListItem,
    ActivityEventFilterOptions,
    ActivityEventProjectMetadata,
    ProjectActivityEventsResponse,
)
from codemie.rest_api.models.base import PaginationData


class TestActivityEventListItem:
    def test_all_fields_present(self):
        item = ActivityEventListItem(
            id="evt-1",
            domain="user_management",
            event_type="user.created",
            label="User Created",
            category="Account",
            entity_type="user",
            entity_id="u-1",
            entity_label="Jane Doe",
            actor_id="a-1",
            actor_email="admin@test.com",
            actor_name="Admin",
            attributes={"key": "val"},
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        assert item.id == "evt-1"
        assert item.actor_email == "admin@test.com"
        assert item.label == "User Created"
        assert item.category == "Account"
        assert item.entity_label == "Jane Doe"

    def test_optional_fields_default_to_none(self):
        item = ActivityEventListItem(
            id="evt-2",
            domain="budget_management",
            event_type="budget.created",
            label="Budget Created",
            category="Budget",
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        assert item.entity_type is None
        assert item.entity_id is None
        assert item.entity_label is None
        assert item.actor_id is None
        assert item.actor_email is None
        assert item.actor_name is None
        assert item.attributes is None
        assert item.project_name is None

    def test_project_name_accepts_value(self):
        item = ActivityEventListItem(
            id="evt-3",
            domain="project_management",
            event_type="project.updated",
            label="Project Updated",
            category="Settings",
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            project_name="acme",
        )
        assert item.project_name == "acme"


class TestProjectActivityEventsResponse:
    def test_builds_envelope_with_recording_enabled(self):
        item = ActivityEventListItem(
            id="evt-1",
            domain="user_management",
            event_type="user.created",
            label="User Created",
            category="Account",
            created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        pagination = PaginationData(page=0, per_page=50, total=1, pages=1)

        response = ProjectActivityEventsResponse(data=[item], pagination=pagination, recording_enabled=True)

        assert response.data[0].id == "evt-1"
        assert response.pagination.total == 1
        assert response.recording_enabled is True

    def test_recording_disabled_with_empty_data(self):
        pagination = PaginationData(page=0, per_page=50, total=0, pages=0)

        response = ProjectActivityEventsResponse(data=[], pagination=pagination, recording_enabled=False)

        assert response.data == []
        assert response.recording_enabled is False


class TestActivityEventFilterOptions:
    def test_all_lists_populated(self):
        opts = ActivityEventFilterOptions(
            domains=["budget_management", "user_management"],
            event_types=["budget.created", "user.created"],
            entity_types=["budget", "user"],
        )
        assert "user_management" in opts.domains
        assert len(opts.event_types) == 2
        assert len(opts.entity_types) == 2

    def test_empty_lists_valid(self):
        opts = ActivityEventFilterOptions(domains=[], event_types=[], entity_types=[])
        assert opts.domains == []


class TestActivityEventProjectMetadata:
    def test_all_fields_present(self):
        metadata = ActivityEventProjectMetadata(
            domains=["budget_management"],
            event_types=[{"event_type": "budget.created", "label": "Budget Created", "category": "Budget"}],
            categories=["Budget", "Members", "Settings", "Account"],
            recording_enabled=True,
        )
        assert metadata.domains == ["budget_management"]
        assert metadata.event_types[0].event_type == "budget.created"
        assert metadata.event_types[0].label == "Budget Created"
        assert metadata.event_types[0].category == "Budget"
        assert metadata.categories == ["Budget", "Members", "Settings", "Account"]
        assert metadata.recording_enabled is True

    def test_disabled_recording_has_empty_event_types(self):
        metadata = ActivityEventProjectMetadata(domains=[], event_types=[], categories=[], recording_enabled=False)
        assert metadata.domains == []
        assert metadata.event_types == []
        assert metadata.recording_enabled is False
