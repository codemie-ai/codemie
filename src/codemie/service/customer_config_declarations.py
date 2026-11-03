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

#: Shared by every field whose declared value must be a positive integer.
_POSITIVE_INTEGER_PATTERN = r"^[1-9][0-9]*$"
_POSITIVE_INTEGER_MESSAGE = "Must be a positive integer"


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
    # Fallback value when neither YAML nor an override supplies one (e.g. a
    # component with no YAML entry, whose real default lives in Python code).
    default: str | bool | None = None

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
        """Value per field when YAML carries no such component: its own `default`, else a neutral placeholder."""

        def _value(field: FieldDeclaration) -> str | bool:
            if field.default is not None:
                return field.default
            return False if field.type is FieldType.SWITCH else ""

        return {field.name: _value(field) for field in self.fields}


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

BANNER = SettingDeclaration(
    component_id="banner",
    label="Banner",
    description="Notice shown across the top of the application for every user.",
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Show banner",
        ),
        FieldDeclaration(
            name="message",
            type=FieldType.TEXTAREA,
            label="Banner message",
            description="Plain text. Line breaks are preserved where the banner is shown.",
            max_length=1000,
        ),
        FieldDeclaration(
            name="linkLabel",
            type=FieldType.INPUT,
            label="Link label",
            description="Shown after the message. The link appears only when both link fields are filled in.",
            max_length=100,
        ),
        FieldDeclaration(
            name="linkRoute",
            type=FieldType.INPUT,
            label="Link target",
            description="Path inside the application, such as /settings/profile, or a full URL.",
            max_length=200,
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
            pattern=_POSITIVE_INTEGER_PATTERN,
            pattern_message=_POSITIVE_INTEGER_MESSAGE,
        ),
    ],
)

_POSITIVE_INT_PATTERN = r"^[1-9][0-9]*$"
_POSITIVE_INT_MESSAGE = (
    "Must be a positive integer (null is not accepted; use DELETE to restore the deployment default)"
)

_FLOAT_01_PATTERN = r"^(0(\.\d+)?|1(\.0*)?)$"
_FLOAT_01_MESSAGE = "Must be a number between 0 and 1 (e.g. 0.85)"

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

CLI_ANALYTICS = SettingDeclaration(
    component_id="features:cliAnalytics",
    label="CLI analytics",
    description="Classification settings for CLI analytics.",
    fields=[
        FieldDeclaration(
            name="corporateOrganizations",
            type=FieldType.TEXTAREA,
            label="Corporate organizations",
            description="One organization name per line. Sessions of a listed organization are classified Corporate.",
            max_length=5000,
        ),
    ],
)

MCP_CUSTOM_SERVERS_DISABLED = SettingDeclaration(
    component_id="mcpCustomServersDisabled",
    label="MCP Custom Servers Disabled",
    description=(
        "When enabled, restricts users to catalog-referenced MCP servers only. "
        "Custom inline MCP server configuration is not permitted."
    ),
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Restrict to catalog MCP servers",
        ),
    ],
)

WORKSPACE_SCRIPT_BRIDGE = SettingDeclaration(
    component_id="features:workspaceScriptBridge",
    label="Workspace script bridge",
    description=(
        "Allows scripts run in the workspace sandbox to call tools during their run. "
        "Applies to runs that start after the change."
    ),
    fields=[
        FieldDeclaration(
            name="enabled",
            type=FieldType.SWITCH,
            label="Enable workspace script bridge",
        ),
        FieldDeclaration(
            name="timeoutSeconds",
            type=FieldType.INPUT,
            label="Script run limit (seconds)",
            description=(
                "Time limit in seconds (at most 480) for a script run with the bridge, counted from the start of the "
                "script. Runs without the bridge keep the ordinary limit. Gateways and streams between the browser "
                "and the backend must stay open longer than this limit."
            ),
            max_length=3,
            pattern=_POSITIVE_INTEGER_PATTERN,
            pattern_message=_POSITIVE_INTEGER_MESSAGE,
        ),
        FieldDeclaration(
            name="maxParallelCalls",
            type=FieldType.INPUT,
            label="Parallel tool calls per run",
            description=(
                "Most tool calls of one script run that are served at the same time (1 serves them one after "
                "another). A ceiling, not a guarantee: all runs of one backend process also share a process-wide "
                "limit. A value above that limit is lowered to it."
            ),
            max_length=3,
            pattern=_POSITIVE_INTEGER_PATTERN,
            pattern_message=_POSITIVE_INTEGER_MESSAGE,
        ),
    ],
)

# Defaults mirror configs/marketplace_management.py's _THRESHOLD_DEFAULTS (no YAML entry exists for this component).
MARKETPLACE_THRESHOLDS = SettingDeclaration(
    component_id="marketplaceThresholds",
    label="Marketplace Thresholds",
    fields=[
        FieldDeclaration(
            name="hotMinUsers",
            type=FieldType.INPUT,
            label="Hot tier min. users",
            description="Minimum active users for the Hot tier.",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
            default="50",
        ),
        FieldDeclaration(
            name="warmMinUsers",
            type=FieldType.INPUT,
            label="Warm tier min. users",
            description="Minimum active users for the Warm tier.",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
            default="7",
        ),
        FieldDeclaration(
            name="coldMinUsers",
            type=FieldType.INPUT,
            label="Cold tier min. users",
            description="Minimum active users for the Cold tier.",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
            default="3",
        ),
        FieldDeclaration(
            name="graceDays",
            type=FieldType.INPUT,
            label="Cold grace period (days)",
            description="Days in Cold tier before cleanup.",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
            default="60",
        ),
        FieldDeclaration(
            name="archiveDeadAfterDays",
            type=FieldType.INPUT,
            label="Dead grace period (days)",
            description="Days in Dead tier before cleanup.",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
            default="30",
        ),
    ],
)

MARKETPLACE_CORE_GATES = SettingDeclaration(
    component_id="marketplaceCoreGates",
    label="Core Gates",
    fields=[
        FieldDeclaration(
            name="descriptionMin",
            type=FieldType.INPUT,
            label="Description min. length",
            description="Minimum number of characters required in the assistant's description.",
            default="100",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
        ),
        FieldDeclaration(
            name="systemPromptMin",
            type=FieldType.INPUT,
            label="System prompt min. length",
            description="Minimum number of characters required in the system prompt.",
            default="200",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
        ),
        FieldDeclaration(
            name="substancePromptAltMin",
            type=FieldType.INPUT,
            label="Substance-prompt alt min. length",
            description="Minimum number of characters required in the substance/alternative prompt.",
            default="500",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
        ),
    ],
)

MARKETPLACE_POLISH_GATES = SettingDeclaration(
    component_id="marketplacePolishGates",
    label="Polish Gates",
    fields=[
        FieldDeclaration(
            name="descriptionMin",
            type=FieldType.INPUT,
            label="Description min. length",
            description="Minimum number of characters required in the assistant's description for the polish check.",
            default="250",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
        ),
        FieldDeclaration(
            name="categoriesMin",
            type=FieldType.INPUT,
            label="Categories min. count",
            description="Minimum number of categories the assistant must be assigned to.",
            default="2",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
        ),
        FieldDeclaration(
            name="requireIcon",
            type=FieldType.SWITCH,
            label="Require icon",
            description="Whether the assistant must have an icon to pass the polish check.",
            default=True,
        ),
        FieldDeclaration(
            name="conversationStartersMin",
            type=FieldType.INPUT,
            label="Conversation starters min. count",
            description="Minimum number of conversation starters the assistant must have.",
            default="2",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
        ),
    ],
)

MARKETPLACE_SIMILARITY = SettingDeclaration(
    component_id="marketplaceSimilarity",
    label="Similarity",
    fields=[
        FieldDeclaration(
            name="criticalThreshold",
            type=FieldType.INPUT,
            label="Critical threshold",
            description="Similarity score (0–1) above which two assistants are treated as near-duplicates.",
            default="0.70",
            pattern=_FLOAT_01_PATTERN,
            pattern_message=_FLOAT_01_MESSAGE,
        ),
        FieldDeclaration(
            name="optionalThreshold",
            type=FieldType.INPUT,
            label="Optional threshold",
            description="Similarity score (0–1) above which two assistants are flagged as worth reviewing for overlap.",
            default="0.55",
            pattern=_FLOAT_01_PATTERN,
            pattern_message=_FLOAT_01_MESSAGE,
        ),
        FieldDeclaration(
            name="reportThreshold",
            type=FieldType.INPUT,
            label="Report threshold",
            description="Similarity score (0–1) above which a pair appears in similarity reports.",
            default="0.40",
            pattern=_FLOAT_01_PATTERN,
            pattern_message=_FLOAT_01_MESSAGE,
        ),
        FieldDeclaration(
            name="shortlistSize",
            type=FieldType.INPUT,
            label="Shortlist size",
            description=(
                "Maximum number of candidate assistants to compare against each assistant during a similarity check."
            ),
            default="10",
            pattern=_POSITIVE_INT_PATTERN,
            pattern_message=_POSITIVE_INT_MESSAGE,
        ),
    ],
)

DECLARATIONS: tuple[SettingDeclaration, ...] = (
    CHAT_DISCLAIMER,
    RELEASE_NOTES_RECENT_COUNT,
    BANNER,
    WEB_SEARCH,
    SCHEDULERS,
    MCP_CUSTOM_SERVERS_DISABLED,
    WORKSPACE_SCRIPT_BRIDGE,
    CLI_ANALYTICS,
    MARKETPLACE_THRESHOLDS,
    MARKETPLACE_CORE_GATES,
    MARKETPLACE_POLISH_GATES,
    MARKETPLACE_SIMILARITY,
)

_BY_COMPONENT_ID = {declaration.component_id: declaration for declaration in DECLARATIONS}
_BY_KEY = {declaration.key: declaration for declaration in DECLARATIONS}


def by_component_id(component_id: str) -> SettingDeclaration | None:
    return _BY_COMPONENT_ID.get(component_id)


def by_key(key: str) -> SettingDeclaration | None:
    return _BY_KEY.get(key)
