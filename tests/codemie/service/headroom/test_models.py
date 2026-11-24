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

import dataclasses

import pytest

from codemie.service.headroom.models import (
    HeadroomConfig,
    HeadroomOverride,
    HeadroomStrategy,
    ResolvedHeadroomConfig,
)


def test_strategy_has_three_values():
    assert {s.value for s in HeadroomStrategy} == {"lossless", "balanced", "aggressive"}
    assert HeadroomStrategy("balanced") is HeadroomStrategy.BALANCED


def test_config_defaults():
    config = HeadroomConfig()

    assert config.enabled is False
    assert config.compress_user_messages is True
    assert config.compress_tool_outputs is True
    assert config.strategy is HeadroomStrategy.BALANCED


def test_config_parses_strategy_from_string():
    assert HeadroomConfig(strategy="aggressive").strategy is HeadroomStrategy.AGGRESSIVE


def test_override_defaults_to_all_none():
    override = HeadroomOverride()

    assert override.enabled is None
    assert override.compress_user_messages is None
    assert override.compress_tool_outputs is None
    assert override.strategy is None


def test_resolved_config_is_frozen():
    resolved = ResolvedHeadroomConfig(
        compress_user_messages=True,
        compress_tool_outputs=False,
        strategy=HeadroomStrategy.LOSSLESS,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        resolved.strategy = HeadroomStrategy.AGGRESSIVE  # type: ignore[misc]
