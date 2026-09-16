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

"""
Client-source context management using ContextVar.

This module validates the ``X-CodeMie-Client`` header against a closed
allow-list, normalizes it into a ClientSource, and stores it in a
request-scoped ContextVar, mirroring the pattern in ``user_context.py``.
"""

from contextvars import ContextVar
from enum import Enum

from codemie.core.constants import HEADER_CODEMIE_CLIENT
from codemie.core.exceptions import ValidationException

_TEAMS_CLIENT_VALUES = frozenset({"teams-bot"})
_PLATFORM_CLIENT_VALUES = frozenset({"web"})
_OTHER_CLIENT_VALUES = frozenset({"sdk", "chrome-extension"})
_VALID_CLIENT_VALUES = _TEAMS_CLIENT_VALUES | _PLATFORM_CLIENT_VALUES | _OTHER_CLIENT_VALUES


class ClientSource(str, Enum):
    """Normalized client source recorded on assistant chat usage metrics."""

    PLATFORM = "platform"
    TEAMS = "teams"
    OTHER = "other"


current_client_source: ContextVar[ClientSource] = ContextVar('current_client_source', default=ClientSource.PLATFORM)


def normalize_client_source(raw: str | None) -> ClientSource:
    """
    Validate and normalize a raw ``X-CodeMie-Client`` header value into a ClientSource.

    Args:
        raw: The raw header value, or None if the header was not sent.

    Returns:
        ClientSource.PLATFORM when the header is absent, blank, or "web";
        ClientSource.TEAMS for "teams-bot"; ClientSource.OTHER for "sdk" or
        "chrome-extension".

    Raises:
        ValidationException: If the header is present, non-blank, and not one
            of the recognized values.
    """
    if not raw:
        return ClientSource.PLATFORM

    normalized = raw.strip().lower()
    if not normalized:
        return ClientSource.PLATFORM
    if normalized in _TEAMS_CLIENT_VALUES:
        return ClientSource.TEAMS
    if normalized in _PLATFORM_CLIENT_VALUES:
        return ClientSource.PLATFORM
    if normalized in _OTHER_CLIENT_VALUES:
        return ClientSource.OTHER
    raise ValidationException(
        f"Invalid {HEADER_CODEMIE_CLIENT} header value: {raw!r}. "
        f"Expected one of: {', '.join(sorted(_VALID_CLIENT_VALUES))}."
    )


def set_client_source(client_source: ClientSource) -> None:
    """Store the normalized client source in the request context."""
    current_client_source.set(client_source)


def get_client_source() -> ClientSource:
    """Retrieve the normalized client source from the request context."""
    return current_client_source.get()


def clear_client_source() -> None:
    """Reset the client source to the default (PLATFORM) in the request context."""
    current_client_source.set(ClientSource.PLATFORM)
