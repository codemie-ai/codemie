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

"""Single strategy table: Headroom strategy -> sidecar config body and SDK CompressConfig kwargs.

No other module may name strategy values; callers go through the two functions below.

Notes:
- Lossless on the sidecar has no per-request mode; it relies on the sidecar running with
  HEADROOM_LOSSLESS=1, so the body only carries the user-message flag.
- Aggressive only differs from balanced where the ML compressor runs (target_ratio is honoured there).
"""

from __future__ import annotations

from typing import Any

from codemie.configs import config
from codemie.service.headroom.models import HeadroomStrategy


def sidecar_config(strategy: HeadroomStrategy, compress_user_messages: bool) -> dict[str, Any]:
    """Build the `config` body sent to the Headroom sidecar for the given strategy."""
    body: dict[str, Any] = {}
    if strategy == HeadroomStrategy.BALANCED:
        body["mode"] = "lossy_inline"
    elif strategy == HeadroomStrategy.AGGRESSIVE:
        body["mode"] = "lossy_inline"
        body["target_ratio"] = float(config.HEADROOM_AGGRESSIVE_TARGET_RATIO)
    body["compress_user_messages"] = compress_user_messages
    return body


def sdk_compress_kwargs(strategy: HeadroomStrategy, compress_user_messages: bool) -> dict[str, Any]:
    """Build the keyword arguments for the SDK `CompressConfig` for the given strategy."""
    kwargs: dict[str, Any] = {}
    if strategy == HeadroomStrategy.LOSSLESS:
        kwargs["mode"] = "agent"
    elif strategy == HeadroomStrategy.AGGRESSIVE:
        kwargs["target_ratio"] = float(config.HEADROOM_AGGRESSIVE_TARGET_RATIO)
    kwargs["compress_user_messages"] = compress_user_messages
    return kwargs
