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

"""Unit tests for HeadroomSavings.merged."""

from __future__ import annotations

from codemie.core.headroom_savings import HeadroomSavings


def _savings(mode: str = "sdk", strategy: str | None = "balanced", saved: int = 60) -> HeadroomSavings:
    return HeadroomSavings(
        tokens_original=100, tokens_compacted=100 - saved, tokens_saved=saved, mode=mode, strategy=strategy
    )


def test_merge_sums_counts_and_keeps_shared_labels():
    merged = _savings().merged(_savings(saved=40))

    assert (merged.tokens_original, merged.tokens_compacted, merged.tokens_saved, merged.calls) == (200, 100, 100, 2)
    assert (merged.mode, merged.strategy) == ("sdk", "balanced")


def test_merge_of_different_strategies_has_no_strategy():
    assert _savings(strategy="balanced").merged(_savings(strategy="aggressive")).strategy is None


def test_merge_of_different_modes_is_labelled_mixed_in_either_order():
    sdk, sidecar = _savings(mode="sdk"), _savings(mode="sidecar")

    assert sdk.merged(sidecar).mode == "mixed"
    assert sidecar.merged(sdk).mode == "mixed"


def test_merge_keeps_mixed_mode_sticky():
    assert _savings(mode="mixed").merged(_savings(mode="sdk")).mode == "mixed"
