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

"""Registry of customer-config components that may be overridden at runtime.

A component absent from this registry is served from YAML only: it is never read from
or written to dynamic configuration. Making a component dynamic means appending a
declaration here — no API, schema or frontend change is required.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator

KEY_PREFIX = "CUSTOMER_CONFIG__"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALPHANUMERIC = re.compile(r"[^A-Za-z0-9]+")


class FieldType(str, Enum):
    """Form control a declared field is rendered with."""

    SWITCH = "switch"
    INPUT = "input"
    TEXTAREA = "textarea"


class Markup(str, Enum):
    """Markup a text field may carry, driving sanitisation on write."""

    PLAIN = "plain"
    MARKDOWN = "markdown"


def build_key(component_id: str) -> str:
    """Derive the dynamic-config key for a customer-config component id.

    ``features:webSearch`` becomes ``CUSTOMER_CONFIG__FEATURES__WEB_SEARCH``: the ``:``
    namespace separator maps to a double underscore, camelCase boundaries become single
    underscores, and everything is uppercased.
    """
    segments = [_to_upper_snake(segment) for segment in component_id.split(":")]
    return KEY_PREFIX + "__".join(segments)


def _to_upper_snake(segment: str) -> str:
    spaced = _CAMEL_BOUNDARY.sub("_", segment)
    return _NON_ALPHANUMERIC.sub("_", spaced).strip("_").upper()


class FieldDeclaration(BaseModel):
    """One editable field inside a component's ``settings`` object."""

    name: str
    type: FieldType
    label: str
    description: str | None = None
    required: bool = False
    max_length: int | None = None
    pattern: str | None = None
    pattern_message: str | None = None
    markup: Markup = Markup.PLAIN

    @field_validator("pattern")
    @classmethod
    def _pattern_must_compile(cls, value: str | None) -> str | None:
        if value is not None:
            re.compile(value)
        return value


class SettingDeclaration(BaseModel):
    """A customer-config component exposed for runtime editing."""

    component_id: str
    label: str
    description: str | None = None
    fields: list[FieldDeclaration] = Field(min_length=1)

    @property
    def key(self) -> str:
        return build_key(self.component_id)

    @property
    def field_names(self) -> set[str]:
        return {field.name for field in self.fields}

    def empty_value(self) -> dict:
        """Neutral value per declared field, used when YAML carries no such component."""
        return {field.name: (False if field.type is FieldType.SWITCH else "") for field in self.fields}


CHAT_DISCLAIMER = SettingDeclaration(
    component_id="chatDisclaimer",
    label="Chat disclaimer",
    description="Short notice shown below the chat message input for every user.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Show disclaimer",
        ),
        FieldDeclaration(
            name="text",
            type=FieldType.TEXTAREA,
            label="Disclaimer text",
            description="Markdown is supported. Block markup is flattened where the text is shown.",
            max_length=1000,
            markup=Markup.MARKDOWN,
        ),
    ],
)

RELEASE_NOTES_RECENT_COUNT = SettingDeclaration(
    component_id="releaseNotesRecentCount",
    label="Release Notes: Recent releases count",
    description="Number of releases shown before archiving into accordion groups.",
    fields=[
        FieldDeclaration(
            name="recentReleaseCount",
            type=FieldType.INPUT,
            label="Recent release count",
            max_length=4,
            pattern=r"^[1-9][0-9]*$",
            pattern_message="Must be a positive integer",
        ),
    ],
)

WEB_SEARCH = SettingDeclaration(
    component_id="features:webSearch",
    label="Web search",
    description="Google Search, Tavily Search and Web Scraper tools in chat.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Enable web search",
        ),
    ],
)

SCHEDULERS = SettingDeclaration(
    component_id="features:schedulersView",
    label="Schedulers",
    description="Controls whether the Schedulers navigation item is visible in the UI.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Enable Schedulers",
        ),
    ],
)

DECLARATIONS: tuple[SettingDeclaration, ...] = (CHAT_DISCLAIMER, RELEASE_NOTES_RECENT_COUNT, WEB_SEARCH, SCHEDULERS)

_BY_COMPONENT_ID = {declaration.component_id: declaration for declaration in DECLARATIONS}
_BY_KEY = {declaration.key: declaration for declaration in DECLARATIONS}


def by_component_id(component_id: str) -> SettingDeclaration | None:
    return _BY_COMPONENT_ID.get(component_id)


def by_key(key: str) -> SettingDeclaration | None:
    return _BY_KEY.get(key)
