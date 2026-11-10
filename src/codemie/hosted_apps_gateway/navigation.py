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

WRAPPER_SCRIPT = r"""(() => {
  const frame = document.querySelector('iframe');
  const content = new URL(frame.dataset.src);
  addEventListener('message', event => {
    if (event.source !== frame.contentWindow || event.origin !== content.origin) return;
    const data = event.data;
    if (!data || data.type !== 'codemie-hosted-location' || typeof data.target !== 'string') return;
    const target = data.target;
    if (target.length > 12288 || !target.startsWith('/') || target.startsWith('//') ||
        /[\\\x00-\x1f\x7f]/.test(target)) return;
    const url = new URL(target, location.origin);
    if (url.origin !== location.origin || url.username || url.password ||
        /^\/_platform(?:\/|$)/i.test(url.pathname)) return;
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  });
  frame.src = content.href + location.hash;
})();"""
WRAPPER_SCRIPT_HASH = base64.b64encode(hashlib.sha256(WRAPPER_SCRIPT.encode()).digest()).decode()


def navigation_suffix(viewer_origin: str) -> bytes:
    """Add a fixed browser hook only to interactive HTML navigation representations.

    Stored version bytes stay immutable. The parent verifies the exact child origin/window,
    and copies only a bounded local path to its address bar. No tokens or app metadata travel.
    """
    return (
        "\n<script>(() => {\n"
        "const report = () => parent.postMessage({type: 'codemie-hosted-location', "
        "target: location.pathname + location.search + location.hash}, " + json.dumps(viewer_origin) + ");\n"
        "for (const name of ['pushState', 'replaceState']) {\n"
        "  const original = history[name];\n"
        "  history[name] = function(...args) { const value = Reflect.apply(original, this, args); "
        "report(); return value; };\n"
        "}\n"
        "addEventListener('popstate', report); addEventListener('hashchange', report); report();\n"
        "})();</script>"
    ).encode()


async def representation_range(storage, key: str, size: int, suffix: bytes, start: int, end: int):
    """Stream a byte range across immutable asset and fixed suffix without buffering the asset."""
    if start < size:
        async for chunk in storage.read_range(key, start, min(end, size - 1)):
            yield chunk
    if suffix and end >= size:
        yield suffix[max(0, start - size) : end - size + 1]
