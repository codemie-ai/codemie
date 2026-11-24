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

"""Tests for the Headroom strategy table."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from codemie.service.headroom.models import HeadroomStrategy
from codemie.service.headroom.compressors.strategies import sdk_compress_kwargs, sidecar_config

RATIO = 0.2


@pytest.fixture(autouse=True)
def aggressive_ratio():
    with patch("codemie.service.headroom.compressors.strategies.config") as mock_config:
        mock_config.HEADROOM_AGGRESSIVE_TARGET_RATIO = RATIO
        yield mock_config


@pytest.mark.parametrize("user", [True, False])
@pytest.mark.parametrize(
    "strategy, expected",
    [
        (HeadroomStrategy.LOSSLESS, {}),
        (HeadroomStrategy.BALANCED, {"mode": "lossy_inline"}),
        (HeadroomStrategy.AGGRESSIVE, {"mode": "lossy_inline", "target_ratio": RATIO}),
    ],
)
def test_sidecar_config_body(strategy, expected, user):
    assert sidecar_config(strategy, user) == {**expected, "compress_user_messages": user}


@pytest.mark.parametrize("user", [True, False])
@pytest.mark.parametrize(
    "strategy, expected",
    [
        (HeadroomStrategy.LOSSLESS, {"mode": "agent"}),
        (HeadroomStrategy.BALANCED, {}),
        (HeadroomStrategy.AGGRESSIVE, {"target_ratio": RATIO}),
    ],
)
def test_sdk_compress_kwargs(strategy, expected, user):
    assert sdk_compress_kwargs(strategy, user) == {**expected, "compress_user_messages": user}


def test_ratio_is_read_at_call_time(aggressive_ratio):
    aggressive_ratio.HEADROOM_AGGRESSIVE_TARGET_RATIO = 0.35

    assert sidecar_config(HeadroomStrategy.AGGRESSIVE, True)["target_ratio"] == 0.35
    assert sdk_compress_kwargs(HeadroomStrategy.AGGRESSIVE, True)["target_ratio"] == 0.35


@pytest.mark.parametrize("strategy", list(HeadroomStrategy))
def test_returned_dicts_are_independent(strategy):
    first_sidecar = sidecar_config(strategy, True)
    first_sidecar["mutated"] = True
    first_sdk = sdk_compress_kwargs(strategy, True)
    first_sdk["mutated"] = True

    assert "mutated" not in sidecar_config(strategy, True)
    assert "mutated" not in sdk_compress_kwargs(strategy, True)
