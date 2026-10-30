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

"""Visibility of ingest-gate discards (EPMCDME-16441): one log line per request.

The HTTP answer of a discard is unchanged. The log line carries the route, the reason code, the number of
dropped records, the number of distinct sessions in them, a capped sample of session ids and, when a key is
configured, a short keyed hash of the sender. Session ids are client-supplied, so the sample is sanitized
(whitespace and control characters become `?`), cut to a fixed length and limited to a few ids. The line never
carries an email, a project name or a raw user id, and the hash is a log field only, never a metric label.
"""

from __future__ import annotations

import logging
from collections.abc import Collection

logger = logging.getLogger(__name__)

_MAX_SAMPLE_IDS = 3
_MAX_ID_LENGTH = 64


def _sample(session_ids: Collection[str]) -> str:
    """Up to three sanitized ids, de-duplicated after sanitizing, sorted, comma-joined."""
    clean = {
        "".join("?" if c.isspace() or not c.isprintable() else c for c in sid)[:_MAX_ID_LENGTH] for sid in session_ids
    }
    return ",".join(sorted(clean)[:_MAX_SAMPLE_IDS])


def record_gate_discard(
    route: str, reason: str, count: int, user_hash: str | None, session_ids: Collection[str] = ()
) -> None:
    """Log one discarded request."""
    ids = {sid for sid in session_ids if sid}
    sessions = f" session_count={len(ids)}" + (f" session_ids={_sample(ids)}" if ids else "")
    suffix = f" user_hash={user_hash}" if user_hash else ""
    logger.info(
        "cli_analytics: request discarded route=%s reason=%s dropped=%d%s%s", route, reason, count, sessions, suffix
    )
