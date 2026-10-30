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
"""Transport-independent input checks for Exchange writes (shared by the Graph and EWS clients).

Every check runs before the mutating request is submitted, inside the client's write method. The client
itself is already built by then (the EWS endpoint's DNS check, or acquiring a Graph token, has already
happened in ``get_write_client``/``__init__``), so these checks are not the first thing to run in a write
tool call, only the last gate before submission. A bad value raises ``InvalidLocalInputError``
(Not applied) with a fixed sentence: the offending value is never echoed.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from codemie_tools.ms_exchange.errors import InvalidLocalInputError

BODY_TEXT = "text"
BODY_HTML = "html"
_BODY_TYPES = frozenset({BODY_TEXT, BODY_HTML})
_MAX_ADDRESS_LENGTH = 254
MAX_CATEGORY_LENGTH = 255  # Outlook's limit for a category name

# local@domain: no whitespace, quotes, angle brackets, commas, semicolons or a second "@"; the domain is
# dot-separated labels of letters, digits and inner hyphens (a dotless domain such as "corp" is allowed).
_LOCAL_PART = re.compile(r"[^\s@<>(),;:\\\"\[\]]+")
_DOMAIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*")


def _valid_address(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > _MAX_ADDRESS_LENGTH:
        return False
    local, sep, domain = value.partition("@")
    return bool(sep) and bool(_LOCAL_PART.fullmatch(local)) and bool(_DOMAIN.fullmatch(domain))


def _is_unpadded_text(value: Any) -> bool:
    """A non-blank string with no leading or trailing whitespace."""
    return isinstance(value, str) and bool(value) and value == value.strip()


def validate_recipients(to: Any) -> list[str]:
    """A non-empty list of syntactically valid addresses, exactly as received and in order (no cap).

    Padded values are rejected, never repaired.
    """
    if not isinstance(to, (list, tuple)) or not to:
        raise InvalidLocalInputError
    if not all(_valid_address(item) for item in to):
        raise InvalidLocalInputError
    return list(to)


def validate_body_type(body_type: Any) -> str:
    """Exactly ``"text"`` or ``"html"``; anything else (including other casing or padding) is rejected."""
    if not isinstance(body_type, str) or body_type not in _BODY_TYPES:
        raise InvalidLocalInputError
    return body_type


def validate_message_id(message_id: Any) -> str:
    """A non-blank message id exactly as received; leading or trailing whitespace is rejected."""
    if not _is_unpadded_text(message_id):
        raise InvalidLocalInputError
    return message_id


def validate_send_input(to: Any, subject: Any, body: Any, body_type: Any) -> tuple[list[str], str]:
    """Validate send arguments together; returns the recipients and the body type, both as received."""
    recipients = validate_recipients(to)
    normalised = validate_body_type(body_type)
    if not isinstance(subject, str) or not isinstance(body, str):
        raise InvalidLocalInputError
    return recipients, normalised


def validate_folder_name(folder: Any) -> str:
    """A non-blank folder name or id exactly as received. Whether the folder exists is decided by the transport."""
    if not _is_unpadded_text(folder):
        raise InvalidLocalInputError
    return folder


def validate_category(category: Any) -> str:
    """One category name: a non-blank string of at most ``MAX_CATEGORY_LENGTH`` characters, as received.

    Control characters (newlines, tabs, NUL ...) are rejected. Case and inner spaces are kept.
    """
    if not _is_unpadded_text(category) or len(category) > MAX_CATEGORY_LENGTH:
        raise InvalidLocalInputError
    if any(unicodedata.category(ch) == "Cc" for ch in category):
        raise InvalidLocalInputError
    return category
