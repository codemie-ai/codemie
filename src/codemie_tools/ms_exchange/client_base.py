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
"""Read-only client contract shared by the Graph and EWS transports.

Both implementations MUST return exactly these shapes (plain dicts, JSON-friendly):

Folder (``list_folders``)::

    {"id": str, "name": str, "total_count": int, "unread_count": int}

Message summary (``list_messages``; newest first, then by id; ``count`` is capped at
``MAX_PAGE``; never marks anything read)::

    {"id": str, "subject": str, "from": str, "received": str | None,   # ISO-8601
     "is_read": bool, "has_attachments": bool}

Message (``get_message``): the summary plus ``"to": list[str]``, ``"cc": list[str]``
and ``"body": str`` (plain-text body). Addresses are rendered ``"Name <address>"``.

Attachment (``list_attachments``)::

    {"id": str, "name": str, "content_type": str, "size": int, "is_file": bool}

``is_file`` is False for item/reference attachments. ``get_attachment_bytes`` returns
raw ``bytes``; ``get_text_body`` returns ``str``; ``check_inbox`` returns None and
raises a typed ``ExchangeError`` on failure (an empty Inbox is a success).

All failures surface as ``codemie_tools.ms_exchange.errors.ExchangeError`` subclasses.
"""

from __future__ import annotations

from typing import Any, Protocol

MAX_PAGE = 50

FolderDict = dict[str, Any]
MessageDict = dict[str, Any]
AttachmentDict = dict[str, Any]


class ExchangeClient(Protocol):
    def list_folders(self) -> list[FolderDict]: ...

    def list_messages(self, folder: str, start: int, count: int) -> list[MessageDict]: ...

    def get_message(self, message_id: str) -> MessageDict: ...

    def check_inbox(self) -> None: ...

    def get_text_body(self, message_id: str) -> str: ...

    def list_attachments(self, message_id: str) -> list[AttachmentDict]: ...

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes: ...
