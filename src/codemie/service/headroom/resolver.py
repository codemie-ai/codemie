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

"""Resolve the effective Headroom config for one LLM call."""

from __future__ import annotations

from codemie.configs.customer_config import customer_config
from codemie.service.headroom.factory import get_compressor_mode
from codemie.service.headroom.models import (
    HeadroomConfig,
    HeadroomOverride,
    ResolvedHeadroomConfig,
)

HEADROOM_FEATURE = "headroom"


def resolve(
    node: HeadroomOverride | None,
    workflow: HeadroomOverride | None,
    assistant: HeadroomConfig | None,
) -> ResolvedHeadroomConfig | None:
    """Merge node, workflow and assistant settings; None means no compression.

    Each field takes the first non-None value of node, workflow, assistant, then the
    HeadroomConfig default. The mode (not "off") and the customer feature flag are
    checked at call time.
    """
    if get_compressor_mode() == "off" or not customer_config.is_feature_enabled(HEADROOM_FEATURE):
        return None

    defaults = HeadroomConfig()
    layers = (node, workflow, assistant)

    def pick(field: str):
        for layer in layers:
            value = getattr(layer, field, None) if layer is not None else None
            if value is not None:
                return value
        return getattr(defaults, field)

    if not pick("enabled"):
        return None

    return ResolvedHeadroomConfig(
        compress_user_messages=pick("compress_user_messages"),
        compress_tool_outputs=pick("compress_tool_outputs"),
        strategy=pick("strategy"),
    )
