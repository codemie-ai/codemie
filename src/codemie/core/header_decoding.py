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

from __future__ import annotations

import logging
from urllib.parse import unquote

logger = logging.getLogger(__name__)


def decode_attribution_header(raw: str | None) -> str | None:
    """Decode a `/`-separated CLI attribution header value (X-CodeMie-Repository/-Branch).

    Each segment is decoded independently with `unquote(..., errors="strict")` and rejoined with
    `/`. `unquote` itself is lenient toward malformed hex-escape syntax (e.g. a bare `%` or `%zz`
    with no valid hex digits following): it passes such segments through unmodified without
    raising, so they never reach the fallback below and are never logged. Only a segment whose
    hex escapes decode to bytes that are not valid UTF-8 raises; that case is caught here, the raw
    segment text is used as a fallback, and a warning is logged so that fallback is auditable
    rather than a silent default. `None` stays `None`; an unencoded value round-trips unchanged.

    A result that is empty or whitespace-only once decoded (e.g. a literal space header, or a
    `%20`-only header) is normalized to `None`, so every consumer's existing truthy/`is not None`
    check correctly treats it as absent rather than a real value.
    """
    if raw is None:
        return None
    segments = []
    for segment in raw.split("/"):
        try:
            segments.append(unquote(segment, errors="strict"))
        except Exception:
            logger.warning(
                "Attribution header segment failed percent-decoding; falling back to raw text for this segment."
            )
            segments.append(segment)
    decoded = "/".join(segments)
    if not decoded.strip():
        return None
    return decoded
