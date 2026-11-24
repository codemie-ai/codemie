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

import importlib.util
from functools import lru_cache
from typing import TYPE_CHECKING

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.service.headroom.compressors.base import HeadroomCompressionError

if TYPE_CHECKING:
    from codemie.service.headroom.compressors.sdk_compressor import SDKCompressor
    from codemie.service.headroom.compressors.sidecar_compressor import SidecarCompressor


def get_compressor_mode() -> str:
    """Return the configured Headroom mode, "off", "sdk" or "sidecar", without building a compressor.

    HEADROOM_MODE is the only switch; it does not depend on LiteLLM.
    """
    mode = config.HEADROOM_MODE
    if mode == "sdk":
        _report_missing_sdk_once()
    return mode


@lru_cache(maxsize=1)
def _report_missing_sdk_once() -> None:
    """Log one error when SDK mode is resolved but `headroom-ai` is not installed (it is an optional extra)."""
    if importlib.util.find_spec("headroom") is None:
        logger.error(
            "Headroom SDK mode is active but the 'headroom-ai' package is not installed: compression is disabled. "
            "Install the headroom-sdk (or headroom-sdk-ml) extra, or set HEADROOM_MODE=sidecar/off."
        )


@lru_cache(maxsize=1)
def get_compressor() -> SidecarCompressor | SDKCompressor:
    """Return the process-wide compressor for the configured mode; the SDK package loads only in "sdk" mode."""
    mode = get_compressor_mode()
    if mode == "sidecar":
        from codemie.service.headroom.compressors.sidecar_compressor import SidecarCompressor

        return SidecarCompressor()
    if mode == "sdk":
        from codemie.service.headroom.compressors.sdk_compressor import SDKCompressor

        return SDKCompressor()
    raise HeadroomCompressionError("Headroom is off")
