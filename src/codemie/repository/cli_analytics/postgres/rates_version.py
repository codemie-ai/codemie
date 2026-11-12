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

"""The version stamp of a catalog rate set.

A stored `cost_key` is only valid for the rate set that priced it, so every derived session carries the version of
that set (`session_dims.rates_version`). The service layer hashes everything its rate resolution reads (the catalog's
entries), so the version changes whenever the rates of any raw model name, alias or not, can change. The version is a
short hash of the rates, equal for equal rate sets whatever their insertion order. The repository never imports the
service layer: the rates arrive as an injected callable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping

from codemie.repository.cli_analytics.ports import ModelRates

_VERSION_LENGTH = 16
_COLUMNS = ("input", "output", "cache_read", "cache_write", "cache_write_1h")


def _number(value: float | None) -> str:
    # A fixed spelling: a missing rate is not a zero rate, and equal floats print alike.
    return "-" if value is None else f"{float(value):.12e}"


def rates_version(rates: Mapping[str, ModelRates]) -> str:
    """Version of a rate set: sha-256 prefix over the models in sorted order and their rates in fixed formatting."""
    lines = (
        "|".join([name, *(_number(getattr(rates[name], column)) for column in _COLUMNS)]) for name in sorted(rates)
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:_VERSION_LENGTH]


# The version of an empty rate set: a catalog without entries, which would price every session at zero.
EMPTY_RATES_VERSION = rates_version({})


class RatesVersionProvider:
    """The current rates version of an injected rates callable; hashes again only when the callable's output changed."""

    def __init__(self, rates: Callable[[], Mapping[str, ModelRates]]) -> None:
        self._rates = rates
        self._seen: dict[str, ModelRates] | None = None
        self._version = ""

    def current(self) -> str:
        rates = self._rates()
        if self._seen is None or self._seen != rates:
            self._version = rates_version(rates)
            self._seen = dict(rates)
        return self._version
