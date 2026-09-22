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

This module normalizes the ``X-CodeMie-Client`` header into a closed set of
client sources and stores it in a request-scoped ContextVar, mirroring the
pattern in ``user_context.py``.
"""

from contextvars import ContextVar
from enum import Enum


class ClientSource(str, Enum):
    """Normalized client source recorded on assistant chat usage metrics."""

    PLATFORM = "platform"
    MS_TEAMS_BOT = "ms-teams-bot"
    CHROME_EXTENSION = "chrome_extension"
    OTHER = "other"


# Known raw `X-CodeMie-Client` values per category. Add new values (or a new
# category) here to recognize them explicitly — anything not listed still
# normalizes to ClientSource.OTHER, so this is documentation/intent, not a
# strict allow-list.
CLIENT_SOURCE_VALUES: dict[ClientSource, frozenset[str]] = {
    ClientSource.MS_TEAMS_BOT: frozenset({"teams-bot"}),
    ClientSource.PLATFORM: frozenset({"web"}),
    ClientSource.CHROME_EXTENSION: frozenset({"chrome-extension"}),
}

CLIENT_SOURCE_LOOKUP: dict[str, ClientSource] = {
    value: source for source, values in CLIENT_SOURCE_VALUES.items() for value in values
}


current_client_source: ContextVar[ClientSource] = ContextVar('current_client_source', default=ClientSource.PLATFORM)


def normalize_client_source(raw: str | None) -> ClientSource:
    """Normalize a raw ``X-CodeMie-Client`` header value into a ClientSource."""
    normalized = raw.strip().lower() if raw else ""

    if not normalized:
        return ClientSource.PLATFORM

    return CLIENT_SOURCE_LOOKUP.get(normalized, ClientSource.OTHER)


def set_client_source(client_source: ClientSource | str | None) -> None:
    """Store the client source in the request context.

    Accepts an already-normalized ClientSource, or a raw ``X-CodeMie-Client``
    header value, which is normalized internally.
    """
    normalized = client_source if isinstance(client_source, ClientSource) else normalize_client_source(client_source)
    current_client_source.set(normalized)


def get_client_source() -> ClientSource:
    """Retrieve the normalized client source from the request context."""
    return current_client_source.get()


def clear_client_source() -> None:
    """Reset the client source to the default (PLATFORM) in the request context."""
    current_client_source.set(ClientSource.PLATFORM)
