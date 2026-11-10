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

"""Explicit projections avoid leaking grants, storage keys, or candidate reports to viewers."""

from __future__ import annotations

from codemie.core.ability import Ability, Action
from codemie.core.hosted_applications import access
from codemie.core.hosted_applications.protocol import etag


def timestamp(value):
    return value.isoformat() if value else None


def can_manage(app, user, grants=None) -> bool:
    """Managers (creator, project admin, platform admin) see and act on the working version."""
    return Action.WRITE in Ability(user, grants=grants).list(app)


def app_projection(app, user, *, grants=None, publisher=None, artifact_kind=None, has_cover=False):
    """The app as ``user`` (a ``platform_user`` copy) sees it; clients derive capabilities from ``user_abilities``."""
    abilities = Ability(user, grants=grants).list(app)
    manager = Action.WRITE in abilities
    created_by = app.created_by
    working_kind = app.artifact_kind if manager else None
    result = {
        "id": str(app.id),
        "name": app.name,
        "description": app.description,
        # A new candidate must not change the cover mode of the selected/live accepted version.
        "artifact_kind": artifact_kind if artifact_kind is not None else working_kind,
        "updated_at": timestamp(app.updated_at),
        "publisher": {"display_name": publisher or "CodeMie user"},
        "project": app.project,
        "shared": bool(app.shared),
        "is_global": bool(app.is_global),
        "created_by": (
            {"id": created_by.id, "username": created_by.username, "name": created_by.name} if created_by else None
        ),
        "user_abilities": [action.value for action in abilities],
        "can_share": access.is_owned_by(created_by.id if created_by else None, user),
        "can_moderate": bool(user.is_admin),
        "suspended": access.is_suspended(app),
        # The actions a suspension refuses right now, from the one backend list (``SUSPEND_GATED_ACTIONS``).
        "suspension_refuses": access.refused_actions(app),
        "has_active_version": app.active_version_id is not None,
        # The manager uploaded a card cover; the image itself is served by the thumbnail endpoint.
        "has_cover": has_cover,
    }
    if manager:
        result.update(
            {
                "selected_version_id": str(app.selected_version_id) if app.selected_version_id else None,
                "active_version_id": str(app.active_version_id) if app.active_version_id else None,
                "lifecycle_state": app.lifecycle_state,
                "revision": app.revision,
                "etag": etag("app", app.id, app.revision),
                "created_at": timestamp(app.created_at),
            }
        )
    return result
