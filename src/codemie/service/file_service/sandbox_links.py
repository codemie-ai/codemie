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


"""Sandbox file links in messages: the one pattern and the one token collector.

Leaf module: it imports nothing from `codemie`, so the agent runtime, the share service and the
download rules can all depend on it without forming a cycle.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

SANDBOX_FILE_PREFIX = "sandbox:/v1/files/"

# Matches a whole sandbox file URL; the backtick is excluded so a URL quoted as `sandbox:...` yields a clean token.
SANDBOX_FILE_RE = re.compile(r"sandbox:/v1/files/[^\s)\]>\"'`]+")


def _tokens_in(text: Any) -> list[str]:
    if not isinstance(text, str) or SANDBOX_FILE_PREFIX not in text:
        return []
    return [match[len(SANDBOX_FILE_PREFIX) :] for match in SANDBOX_FILE_RE.findall(text)]


def collect_message_file_tokens(messages: Iterable[Any]) -> list[str]:
    """Return every encoded file token the given history messages refer to.

    Two carriers per message: `file_names`, which holds bare encoded tokens, and inline
    `sandbox:/v1/files/<token>` URLs inside the message text. Thoughts are not scanned. The share
    endpoint issues a grant for each token this returns and the file endpoint authorizes against
    the same set, so both sides must derive it from this one function — a substring match on the
    message text would instead grant a token that merely prefixes a longer one.
    """
    tokens: list[str] = []
    for message in messages:
        tokens.extend(getattr(message, "file_names", None) or [])
        tokens.extend(_tokens_in(getattr(message, "message", None)))
    return tokens
