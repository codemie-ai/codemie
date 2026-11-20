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

import inspect

import pytest
from pydantic import BaseModel

from codemie.service.activity import activity_models
from codemie.service.activity.activity_models import (
    BudgetManagementEvent,
    ProjectManagementEvent,
    UserManagementEvent,
)
from codemie.service.activity.activity_taxonomy import (
    CATEGORIES,
    CATEGORY_EVENT_TYPES,
    EVENT_TAXONOMY,
    get_category,
    get_label,
)
from codemie.service.permission.named_grant_types import NAMED_GRANT_TYPES, descriptor_for
from codemie.service.permission.share_link_types import SHARE_LINK_TYPES, link_descriptor_for


def _event_type_values(event_class):
    """Collect plain-string constant values defined on an event constants class."""
    return [value for name, value in vars(event_class).items() if not name.startswith("_") and isinstance(value, str)]


# Discovered rather than listed, so a new `*Event` constants class cannot silently escape the taxonomy check.
ALL_EVENT_CLASSES = tuple(
    cls
    for name, cls in inspect.getmembers(activity_models, inspect.isclass)
    if cls.__module__ == activity_models.__name__ and name.endswith("Event") and not issubclass(cls, BaseModel)
)


def descriptor_event_type_values():
    """Event types supplied by named-grant and share-link descriptors, discovered through their registries."""
    values = []
    for resource_type in NAMED_GRANT_TYPES:
        grant = descriptor_for(resource_type)
        values.extend([grant.event_added, grant.event_removed])
    for resource_type in SHARE_LINK_TYPES:
        link = link_descriptor_for(resource_type)
        values.extend([link.event_created, link.event_revoked])
    return values


def all_event_type_values():
    values = descriptor_event_type_values()
    for event_class in ALL_EVENT_CLASSES:
        values.extend(_event_type_values(event_class))
    return values


@pytest.mark.parametrize("event_type", all_event_type_values())
def test_every_known_event_type_has_taxonomy_entry(event_type):
    assert event_type in EVENT_TAXONOMY
    label, category = EVENT_TAXONOMY[event_type]
    assert isinstance(label, str) and label != ""
    assert category in CATEGORIES


@pytest.mark.parametrize("event_type", sorted(EVENT_TAXONOMY))
def test_every_taxonomy_entry_resolves_to_a_label_and_a_known_category(event_type):
    assert get_label(event_type).strip() != ""
    assert get_category(event_type) in CATEGORIES


def test_event_classes_are_discovered():
    assert {cls.__name__ for cls in ALL_EVENT_CLASSES} >= {"ProjectManagementEvent", "MarketplaceModerationEvent"}


def test_get_label_returns_taxonomy_label():
    assert get_label(ProjectManagementEvent.PROJECT_CREATED) == "Project Created"


def test_get_category_returns_taxonomy_category():
    assert get_category(ProjectManagementEvent.PROJECT_CREATED) == "Settings"


def test_get_label_falls_back_to_event_type_for_unknown_event_type():
    assert get_label("no.such.event") == "no.such.event"


def test_get_category_falls_back_to_other_for_unknown_event_type():
    assert get_category("no.such.event") == "Other"


def test_descriptor_event_types_are_discovered():
    assert {
        "hosted_application.grant.added",
        "hosted_application.grant.removed",
        "hosted_application.share_link.created",
        "hosted_application.share_link.revoked",
    } <= set(descriptor_event_type_values())


def test_category_event_types_is_inverse_of_taxonomy():
    for event_type, (_, category) in EVENT_TAXONOMY.items():
        assert event_type in CATEGORY_EVENT_TYPES[category]

    for category, event_types in CATEGORY_EVENT_TYPES.items():
        for event_type in event_types:
            assert EVENT_TAXONOMY[event_type][1] == category


def test_user_project_events_are_members_category():
    assert get_category(UserManagementEvent.USER_PROJECT_ASSIGNED) == "Members"
    assert get_category(UserManagementEvent.USER_PROJECT_REMOVED) == "Members"
    assert get_category(UserManagementEvent.USER_PROJECT_ROLE_UPDATED) == "Members"


def test_account_lifecycle_user_events_are_account_category():
    assert get_category(UserManagementEvent.USER_CREATED) == "Account"
    assert get_category(UserManagementEvent.USER_UPDATED) == "Account"
    assert get_category(UserManagementEvent.USER_DEACTIVATED) == "Account"
    assert get_category(UserManagementEvent.USER_LOGIN) == "Account"
    assert get_category(UserManagementEvent.USER_LOGOUT) == "Account"


def test_budget_events_are_budget_category():
    assert get_category(BudgetManagementEvent.BUDGET_CREATED) == "Budget"
    assert get_category(BudgetManagementEvent.PROJECT_BUDGET_GROUP_RESET) == "Budget"


def test_emitting_event_type_without_taxonomy_entry_logs_error(caplog):
    from unittest.mock import patch

    from codemie.service.activity.activity_models import ActivityEventCreate

    with patch("codemie.service.activity.activity_models.logger") as log:
        ActivityEventCreate(domain="x", event_type="no.such.event")
        ActivityEventCreate(domain="x", event_type=ProjectManagementEvent.PROJECT_CREATED)
    log.error.assert_called_once()
    assert "no.such.event" in log.error.call_args[0][0]


def test_entity_label_is_truncated_to_column_width():
    from codemie.service.activity.activity_models import ActivityEventCreate

    event = ActivityEventCreate(
        domain="x", event_type="project.created", entity_type="project", entity_id="p", entity_label="a" * 300
    )
    assert len(event.entity_label) == 255
