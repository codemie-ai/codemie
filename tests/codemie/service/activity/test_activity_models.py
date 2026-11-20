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

import pytest
from pydantic import ValidationError
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEventCreate,
    ProjectManagementEvent,
)


def test_entity_pair_both_set_is_valid():
    e = ActivityEventCreate(
        domain="user_management",
        event_type="user.created",
        entity_type="user",
        entity_id="abc-123",
    )
    assert e.entity_type == "user"
    assert e.entity_id == "abc-123"


def test_entity_pair_both_none_is_valid():
    e = ActivityEventCreate(domain="user_management", event_type="user.login")
    assert e.entity_type is None
    assert e.entity_id is None


def test_entity_type_set_without_entity_id_raises():
    with pytest.raises(ValidationError, match="entity_type and entity_id"):
        ActivityEventCreate(
            domain="user_management",
            event_type="user.created",
            entity_type="user",
            entity_id=None,
        )


def test_entity_id_set_without_entity_type_raises():
    with pytest.raises(ValidationError, match="entity_type and entity_id"):
        ActivityEventCreate(
            domain="user_management",
            event_type="user.created",
            entity_type=None,
            entity_id="abc-123",
        )


def test_activity_event_create_accepts_project_and_actor_snapshot_fields():
    event = ActivityEventCreate(
        domain=ActivityDomain.PROJECT_MANAGEMENT,
        event_type=ProjectManagementEvent.PROJECT_CREATED,
        project_name="acme",
        actor_email="a@b.com",
        actor_display_name="A B",
    )
    assert event.project_name == "acme"
    assert event.actor_email == "a@b.com"
    assert event.actor_display_name == "A B"


def test_activity_event_create_defaults_project_and_actor_snapshot_to_none():
    event = ActivityEventCreate(domain="d", event_type="e")
    assert event.project_name is None
    assert event.actor_email is None
    assert event.actor_display_name is None


def test_entity_label_without_entity_type_raises():
    with pytest.raises(ValidationError, match="entity_label"):
        ActivityEventCreate(
            domain="user_management",
            event_type="user.created",
            entity_label="x",
        )


def test_actor_id_without_actor_email_raises_but_empty_email_is_allowed():
    with pytest.raises(ValidationError, match="actor_email is required"):
        ActivityEventCreate(domain="user_management", event_type="user.updated", actor_id="u-1")
    assert ActivityEventCreate(domain="user_management", event_type="user.updated", actor_id="u-1", actor_email="")
    assert ActivityEventCreate(domain="user_management", event_type="user.updated")
