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
# Route report appended to every interactive HTML navigation, and the window broker appended after
# it for an embedded launch only. Both run inside the function that ``navigation_suffix`` opens.
LOCATION_SCRIPT = inline_script("content_hook.js")
BROKER_SCRIPT = inline_script("content_broker.js")


def script_string(value) -> str:
    """``value`` as a JSON literal that cannot close the inline script element it is written into."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def navigation_suffix(viewer_origin: str, ui_origins=(), *, embedded: bool = False) -> bytes:
    """Add a fixed browser hook only to interactive HTML navigation representations.

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


async def representation_range(storage, key: str, size: int, suffix: bytes, start: int, end: int):
    """Stream a byte range across immutable asset and fixed suffix without buffering the asset."""
    if start < size:
        async for chunk in storage.read_range(key, start, min(end, size - 1)):
            yield chunk
    if suffix and end >= size:
        yield suffix[max(0, start - size) : end - size + 1]
