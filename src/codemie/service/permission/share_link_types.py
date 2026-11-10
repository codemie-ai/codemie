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

"""Registry of resource types that can be shared by link (``share_links``), with each type's link rules.

A link type must also be a named-grant type (``NAMED_GRANT_TYPES``): link recipients are named grants, and the link
service reads the type's grant descriptor for the activity domain and entity type, the owner rule and its refusal
message, the pre-check (``before_grant``), the audit attributes and the recipient rule. The link descriptor holds only
what is specific to links: the link events, the lock and readability checks of an open, the link answers and who may
revoke the live link.

A type listed here is resolved lazily from a dotted path, so importing this module imports no resource module.
Only hosted applications share by link today; assistants and conversations do not use this table.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from codemie.rest_api.models.permission import ResourceType
from codemie.service.permission.named_grant_types import NAMED_GRANT_TYPES


@dataclass(frozen=True)
class ShareLinkDescriptor:
    event_created: str
    event_revoked: str
    # (context, resource_id, opener_id) -> whether the opener can already read the resource (then nothing is written).
    areadable: Callable[[Any, Any, str], Awaitable[bool]]
    # (context, resource_id) -> the resource locked for a link grant, or None when it may not receive one now.
    alock: Callable[[Any, Any], Awaitable[Any | None]]
    project_of: Callable[[Any], str | None]
    # One answer for every unusable link (unknown, revoked, resource gone or unavailable).
    not_available: Callable[[], Exception]
    # The opener does not meet the type's recipient rule (the grant descriptor's ``recipient``).
    opener_refused: Callable[[], Exception]
    # The type's 403 answer for a message (the grant descriptor's ``not_owner_message`` on a create or revoke).
    access_denied: Callable[[str], Exception]
    # Who may revoke the live link: (resource, actor) -> bool on the locked row; None means the grant owner rule.
    may_revoke: Callable[[Any, Any], bool] | None = None


# Dotted "module:attribute" paths, imported on first use.
SHARE_LINK_TYPES: dict[ResourceType, str] = {
    ResourceType.HOSTED_APPLICATION: "codemie.service.hosted_applications.share_link_descriptor:DESCRIPTOR",
}

_cache: dict[ResourceType, ShareLinkDescriptor] = {}


def link_descriptor_for(resource_type: ResourceType) -> ShareLinkDescriptor:
    """The descriptor of a link-sharing type; KeyError for a type that has no share links or no named grants."""
    if resource_type not in NAMED_GRANT_TYPES:
        raise KeyError(f"{resource_type} shares by link but is not a named-grant type")
    if resource_type not in _cache:
        module_name, attribute = SHARE_LINK_TYPES[resource_type].split(":")
        _cache[resource_type] = getattr(importlib.import_module(module_name), attribute)
    return _cache[resource_type]
