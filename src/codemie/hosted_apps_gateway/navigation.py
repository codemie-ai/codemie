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

"""Bounded route hints for standalone viewing; hints never authorize content."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path


def inline_script(name: str) -> str:
    """The exact text of a browser script shipped next to this module, for an inline ``<script>``.

    Read as bytes, so the text (and the wrapper's CSP hash) never depends on the platform's newline
    translation. The leading license comment block stays in the file and is not sent to browsers.
    A script that could end or open a script element is refused at import time.
    """
    text = Path(__file__).with_name(name).read_bytes().decode("utf-8")
    if text.startswith("// Copyright "):
        text = text.split("\n\n", 1)[1]
    lowered = text.lower()
    if "</script" in lowered or "<script" in lowered or "<!--" in lowered:
        raise RuntimeError(f"{name} cannot be inlined into a script element")
    return text


# Own-tab w- wrapper; its CSP authorizes it by the SHA-256 of these exact bytes.
WRAPPER_SCRIPT = inline_script("viewer_wrapper.js")
WRAPPER_SCRIPT_HASH = base64.b64encode(hashlib.sha256(WRAPPER_SCRIPT.encode()).digest()).decode()
# Route report put into every interactive HTML navigation, and the window broker after it for an
# embedded launch only. Both run inside the function that ``navigation_suffix`` opens, before any
# page script (``injection_offset``).
LOCATION_SCRIPT = inline_script("content_hook.js")
BROKER_SCRIPT = inline_script("content_broker.js")


def script_string(value) -> str:
    """``value`` as a JSON literal that cannot close the inline script element it is written into."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def navigation_suffix(viewer_origin: str, ui_origins=(), *, embedded: bool = False) -> bytes:
    """The fixed browser hook for interactive HTML navigation representations only.

    It is inserted at ``injection_offset``, before the page's first script.
    Stored version bytes stay immutable. The parent verifies the exact child origin/window,
    and copies only a bounded local path to its address bar. No tokens or app metadata travel.
    ``embedded`` (a launch inside a CodeMie page) adds the window broker: same-origin ``window.open``
    calls and new-tab links ask the CodeMie page to open that page in a new CodeMie window.
    """
    return (
        "\n<script>(() => {\n"
        + "const viewer = "
        + script_string(viewer_origin)
        + ", ui = "
        + script_string(list(ui_origins))
        + ", embedded = "
        + ("true" if embedded else "false")
        + ";\n"
        + LOCATION_SCRIPT
        + (BROKER_SCRIPT if embedded else "")
        + "})();</script>"
    ).encode()


# How much of an HTML document is searched for the place of the hook; <head> is normally near the top.
SCAN_BYTES = 16384
# The rest of a start tag after its name, with quoted attribute values that may contain ">".
_TAG_REST = rb"(?=[\s/>])(?:[^>\"']|\"[^\"]*\"|'[^']*')*>"
# A comment runs to its first "-->", or to the end of the prefix when unclosed. Each step takes a non-dash or a
# dash that does not start "->", so the match is linear and needs no reluctant quantifier.
_COMMENT = re.compile(rb"<!--(?:[^-]|-(?!->))*(?:-->|\Z)")
_DOCTYPE = re.compile(rb"<!doctype(?=[\s>])[^>]*>", re.I)
_HEAD = re.compile(rb"<head" + _TAG_REST, re.I)
_META = re.compile(rb"<meta" + _TAG_REST, re.I)
_SCRIPT = re.compile(rb"<script(?=[\s/>])", re.I)


def injection_offset(prefix: bytes, size: int) -> int:
    """Where the hook goes in an HTML document of ``size`` bytes that starts with ``prefix``.

    Before the first page script, so ``window.open`` and ``window.opener`` are already in place while the
    page boots: right after ``<meta charset>`` (a declaration within the first 1024 bytes stays there),
    else right after ``<head ...>``, else after the doctype, else at the very start (after a UTF-8 byte
    order mark). Markup inside comments does not count. A UTF-16 document keeps the hook at its end.
    """
    if prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        return size
    comments = [match.span() for match in _COMMENT.finditer(prefix)]

    def first(pattern, accept=lambda match: True):
        for match in pattern.finditer(prefix):
            if not any(start <= match.start() < end for start, end in comments) and accept(match):
                return match
        return None

    script = first(_SCRIPT)
    limit = script.start() if script else len(prefix)
    meta = first(_META, lambda match: match.start() < 1024 and b"charset" in match.group().lower())
    for match in (meta, first(_HEAD), first(_DOCTYPE)):
        if match is not None and match.end() <= limit:
            return match.end()
    return 3 if prefix.startswith(b"\xef\xbb\xbf") else 0


async def representation_range(
    storage, key: str, size: int, suffix: bytes, start: int, end: int, offset: int | None = None
):
    """Stream a byte range of the asset with ``suffix`` inserted at ``offset`` (its end by default).

    The representation is ``asset[:offset] + suffix + asset[offset:]``; the asset is never buffered.
    """
    offset = size if offset is None else offset
    length = len(suffix)
    if start < offset:
        async for chunk in storage.read_range(key, start, min(end, offset - 1)):
            yield chunk
    if length and start < offset + length and end >= offset:
        yield suffix[max(0, start - offset) : end - offset + 1]
    if offset < size and end >= offset + length:
        async for chunk in storage.read_range(key, max(start, offset + length) - length, end - length):
            yield chunk
