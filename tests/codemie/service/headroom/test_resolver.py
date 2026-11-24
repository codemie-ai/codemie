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

"""Tests for the Headroom config resolver."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from codemie.service.headroom.models import (
    HeadroomConfig,
    HeadroomOverride,
    HeadroomStrategy,
    ResolvedHeadroomConfig,
)
from codemie.service.headroom.resolver import resolve


@pytest.fixture(autouse=True)
def gates_on():
    with (
        patch("codemie.service.headroom.factory.config") as mock_config,
        patch("codemie.service.headroom.resolver.customer_config") as mock_customer_config,
    ):
        mock_config.HEADROOM_MODE = "sdk"
        mock_customer_config.is_feature_enabled.return_value = True
        yield mock_config, mock_customer_config


def _on_assistant(**kwargs) -> HeadroomConfig:
    return HeadroomConfig(enabled=True, **kwargs)


def test_all_none_inputs_resolve_to_none():
    assert resolve(None, None, None) is None


def test_assistant_only_uses_assistant_values():
    assistant = _on_assistant(
        compress_user_messages=False,
        compress_tool_outputs=False,
        strategy=HeadroomStrategy.AGGRESSIVE,
    )

    assert resolve(None, None, assistant) == ResolvedHeadroomConfig(
        compress_user_messages=False,
        compress_tool_outputs=False,
        strategy=HeadroomStrategy.AGGRESSIVE,
    )


@pytest.mark.parametrize(
    ("field", "node_value", "workflow_value", "assistant_value", "expected"),
    [
        ("compress_user_messages", False, True, True, False),
        ("compress_user_messages", None, False, True, False),
        ("compress_user_messages", None, None, False, False),
        ("compress_tool_outputs", False, True, True, False),
        ("compress_tool_outputs", None, False, True, False),
        ("compress_tool_outputs", None, None, False, False),
        (
            "strategy",
            HeadroomStrategy.AGGRESSIVE,
            HeadroomStrategy.LOSSLESS,
            HeadroomStrategy.BALANCED,
            HeadroomStrategy.AGGRESSIVE,
        ),
        ("strategy", None, HeadroomStrategy.LOSSLESS, HeadroomStrategy.AGGRESSIVE, HeadroomStrategy.LOSSLESS),
        ("strategy", None, None, HeadroomStrategy.LOSSLESS, HeadroomStrategy.LOSSLESS),
    ],
)
def test_field_precedence_node_then_workflow_then_assistant(
    field, node_value, workflow_value, assistant_value, expected
):
    node = HeadroomOverride(**{field: node_value})
    workflow = HeadroomOverride(**{field: workflow_value})
    assistant = _on_assistant(**{field: assistant_value})

    resolved = resolve(node, workflow, assistant)

    assert resolved is not None
    assert getattr(resolved, field) == expected


def test_enabled_precedence_node_then_workflow_then_assistant():
    off_assistant = HeadroomConfig(enabled=False)

    assert resolve(None, HeadroomOverride(enabled=True), off_assistant) is not None
    assert resolve(HeadroomOverride(enabled=True), None, off_assistant) is not None
    assert resolve(None, None, off_assistant) is None


def test_workflow_enabled_alone_keeps_assistant_strategy():
    assistant = HeadroomConfig(enabled=False, strategy=HeadroomStrategy.AGGRESSIVE)

    resolved = resolve(None, HeadroomOverride(enabled=True), assistant)

    assert resolved is not None
    assert resolved.strategy is HeadroomStrategy.AGGRESSIVE


def test_workflow_enabled_without_assistant_uses_defaults():
    resolved = resolve(None, HeadroomOverride(enabled=True), None)

    assert resolved == ResolvedHeadroomConfig(
        compress_user_messages=True,
        compress_tool_outputs=True,
        strategy=HeadroomStrategy.BALANCED,
    )


def test_node_disabled_overrides_workflow_enabled():
    assert resolve(HeadroomOverride(enabled=False), HeadroomOverride(enabled=True), None) is None


def test_node_enabled_with_no_workflow_block():
    assert resolve(HeadroomOverride(enabled=True), None, None) is not None


def test_kill_switch_off_resolves_to_none(gates_on):
    mock_config, _ = gates_on
    mock_config.HEADROOM_MODE = "off"

    assert resolve(HeadroomOverride(enabled=True), None, _on_assistant()) is None


def test_feature_flag_off_resolves_to_none(gates_on):
    _, mock_customer_config = gates_on
    mock_customer_config.is_feature_enabled.return_value = False

    assert resolve(HeadroomOverride(enabled=True), None, _on_assistant()) is None
    mock_customer_config.is_feature_enabled.assert_called_with("headroom")


def test_merged_disabled_resolves_to_none():
    assert resolve(None, HeadroomOverride(enabled=False), _on_assistant()) is None


@pytest.mark.parametrize("mode", ["sdk", "sidecar"])
def test_active_mode_resolves(gates_on, mode):
    mock_config, _ = gates_on
    mock_config.HEADROOM_MODE = mode

    assert resolve(None, None, _on_assistant()) is not None
