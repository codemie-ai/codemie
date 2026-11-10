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

"""Registry of resource types whose named grants are served by the generic grant service.

A type listed here is resolved lazily from a dotted path, so importing this module imports no resource module.
Neither ``core`` nor ``rest_api.models`` imports it. A named grant is a USER grant of a type listed here
(``is_named_grant``); the generic permission API decides by (resource type, principal type), so a type may also keep
APPLICATION (project) grants in the resource lookup.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from codemie.rest_api.models.permission import PrincipalType, ResourceType
from codemie.rest_api.models.user_management import eligible_internal_user

NOT_OWNER = "Only the owner can share or revoke access"


def _same_type(column: Any) -> Any:
    return column


def _no_attributes(resource_id: Any) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class ResolvedResource:
    """What the generic service needs to know about one grantable resource and the acting user."""

    project: str | None
    creator_id: str | None
    may_remove: bool


@dataclass(frozen=True)
class NamedGrantDescriptor:
    # Class name ``Ability`` loads and matches grants for (``PreloadedGrants.class_name``, ``resource_type_for_class``).
    owned_class_name: str
    activity_domain: Any
    activity_entity_type: Any
    event_added: str
    event_removed: str
    # (session, resource_id, actor) -> project, creator and whether the actor may remove one grant (generic delete).
    resolve: Callable[[Any, Any, Any], ResolvedResource]
    aresolve: Callable[[Any, Any, Any], Awaitable[ResolvedResource]]
    before_grant: Callable[[Any], None]
    # (resource, actor) -> whether the actor owns the resource; the shared grant/revoke refuse anyone else.
    is_owner: Callable[[Any, Any], bool]
    not_owner_message: str = NOT_OWNER
    # ``permissions.resource_id`` (uuid) as the resource table's id type, for SQL that compares the two columns.
    resource_key: Callable[[Any], Any] = _same_type
    # Type-specific attributes added to every grant event (kept for consumers of an older payload).
    audit_attributes: Callable[[Any], dict[str, Any]] = _no_attributes
    # Recipient policy: (users table) -> SQL condition a grantee's ``users`` row must meet, both to be granted and
    # for a stored grant to count (``live_grants``). Internal-only (active, not deleted, not external) by default.
    recipient: Callable[[Any], Any] = eligible_internal_user


# Dotted "module:attribute" paths, imported on first use.
NAMED_GRANT_TYPES: dict[ResourceType, str] = {
    ResourceType.ASSISTANT: "codemie.service.assistant.grant_descriptor:DESCRIPTOR",
    ResourceType.HOSTED_APPLICATION: "codemie.service.hosted_applications.grant_descriptor:DESCRIPTOR",
}

_cache: dict[ResourceType, NamedGrantDescriptor] = {}


def is_named_grant(resource_type: ResourceType, principal_type: PrincipalType | str) -> bool:
    """A USER grant of a registered type: served here; any other row goes to the generic permission path."""
    return resource_type in NAMED_GRANT_TYPES and principal_type == PrincipalType.USER


def resource_type_for_class(class_name: str) -> ResourceType | None:
    """The named-grant type whose descriptor owns ``class_name`` (``Ability``'s class name), or None."""
    for resource_type in NAMED_GRANT_TYPES:
        if descriptor_for(resource_type).owned_class_name == class_name:
            return resource_type
    return None


def descriptor_for(resource_type: ResourceType) -> NamedGrantDescriptor:
    """The descriptor of a named-grant type; KeyError for a type that is not served here."""
    if resource_type not in _cache:
        path = NAMED_GRANT_TYPES[resource_type]
        module_name, attribute = path.split(":")
        _cache[resource_type] = getattr(importlib.import_module(module_name), attribute)
    return _cache[resource_type]
