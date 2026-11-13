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

"""The rates-version stamp and the shared kind masks behind the stored session sort keys."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from codemie.repository.cli_analytics.ports import ModelRates
from codemie.repository.cli_analytics.postgres import cost_key_sql, rates_version as rates_version_module
from codemie.repository.cli_analytics.postgres.kind_masks import KIND_BITS, kind_mask_sql
from codemie.repository.cli_analytics.postgres.rates_version import RatesVersionProvider, rates_version


def _rates(*models: ModelRates) -> dict[str, ModelRates]:
    return {m.model: m for m in models}


A = ModelRates("a", 1e-6, 2e-6, 3e-7, 4e-6, 5e-6)
B = ModelRates("b", 1.5e-6, None, None, None, None)


def test_equal_rate_sets_in_any_insertion_order_have_one_version() -> None:
    assert rates_version(_rates(A, B)) == rates_version(_rates(B, A))


def test_one_changed_rate_changes_the_version() -> None:
    changed = ModelRates("b", 1.5e-6, 1e-9, None, None, None)
    assert rates_version(_rates(A, B)) != rates_version(_rates(A, changed))


def test_a_missing_rate_differs_from_a_zero_rate() -> None:
    assert rates_version(_rates(ModelRates("m", None))) != rates_version(_rates(ModelRates("m", 0.0)))


def test_a_float_and_an_equal_decimal_spelling_give_one_version() -> None:
    assert rates_version(_rates(ModelRates("m", 1e-6))) == rates_version(_rates(ModelRates("m", 0.000001)))


def test_the_version_is_a_short_hex_prefix_of_sha256() -> None:
    version = rates_version({})
    assert len(version) == 16
    assert version == hashlib.sha256(b"").hexdigest()[:16]


def test_provider_hashes_once_while_the_rates_stay_equal() -> None:
    provider = RatesVersionProvider(lambda: _rates(A, B))
    with patch.object(rates_version_module, "rates_version", wraps=rates_version) as spy:
        first = provider.current()
        second = RatesVersionProvider(lambda: _rates(A, B)).current()
        assert provider.current() == first
        assert provider.current() == first
    assert spy.call_count == 1 + 1  # one per provider instance, none for the repeated calls
    assert first == second


def test_provider_follows_the_callable_when_the_rates_change() -> None:
    state = {"rates": _rates(A)}
    provider = RatesVersionProvider(lambda: state["rates"])
    first = provider.current()
    state["rates"] = _rates(A, B)
    assert provider.current() != first


def test_kind_bits_are_distinct_powers_of_two_in_kind_order() -> None:
    assert list(KIND_BITS) == ["input", "output", "cache_read", "cache_write", "cache_write_1h"]
    assert list(KIND_BITS.values()) == [1, 2, 4, 8, 16]


def test_kind_mask_sql_sums_one_bit_per_used_kind() -> None:
    sql = kind_mask_sql()
    for bit in KIND_BITS.values():
        assert f"THEN {bit} ELSE 0" in sql
    assert sql.count("CASE WHEN") == len(KIND_BITS)


def test_cost_key_sql_text_is_unchanged_by_the_shared_definition() -> None:
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    assert {
        "tokens": digest(cost_key_sql.model_token_groups()),
        "tokens_spend": digest(cost_key_sql.model_token_groups(True)),
        "key": digest(cost_key_sql.session_cost_key_sql()),
        "flags": digest(cost_key_sql.model_flags_sql()),
        "cte": digest(cost_key_sql.cost_groups_cte()),
        "key_from_groups": digest(cost_key_sql.cost_key_from_groups_sql()),
        "flags_from_groups": digest(cost_key_sql.kind_flags_from_groups_sql()),
    } == {  # tokens, tokens_spend and key moved with EPMCDME-16633's route-aware normalized_model_name, and again
        # with EPMCDME-16457's dashing of dotted Claude versions
        "tokens": "6437031b0a175a31",
        "tokens_spend": "6386e2c67ecf2937",
        "key": "658929ae3a6dff2b",
        "flags": "5fb955ef2e9eb096",
        "cte": "314a2ed0b8e06824",
        "key_from_groups": "fbde8947bbc1dd3a",
        "flags_from_groups": "4b36483840fb7724",
    }


def test_set_rates_is_kept_by_the_runtime_the_maintainer_and_the_reader() -> None:
    from unittest.mock import MagicMock

    from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS
    from codemie.repository.cli_analytics.postgres.maintenance import PartitionMaintainer
    from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
    from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime

    def version() -> str:
        return "v"

    maintainer = PartitionMaintainer(MagicMock(), MagicMock())
    maintainer.set_rates(version)
    assert maintainer._rates_version is version

    reader = PostgresCliAnalyticsReader(MagicMock())
    reader.set_rates(version)
    assert reader._rates_version is version

    runtime = PostgresAnalyticsRuntime(MagicMock(), SETTINGS, migrate=MagicMock())
    runtime.set_rates(version)
    assert runtime._rates_version is version
    assert runtime._maintainer._rates_version is version


def test_the_runtime_hands_the_rates_to_the_refresher_and_the_maintainer_and_a_rebuilt_refresher_keeps_them() -> None:
    from unittest.mock import MagicMock

    from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS
    from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime

    def version() -> str:
        return "v"

    def rates(names):
        return {}

    runtime = PostgresAnalyticsRuntime(MagicMock(), SETTINGS, migrate=MagicMock())

    runtime.set_rates(version, rates)
    # without these two callables no stored key is written: new sessions stay unstamped and reads fall back to live keys
    for holder in (runtime._refresher, runtime._maintainer):
        assert holder._rates_version is version and holder._rates is rates

    refresher_before = runtime._refresher
    runtime.set_classifier(lambda names: "framework")  # the classifier rebuilds the refresher ...
    assert runtime._refresher is not refresher_before
    assert runtime._refresher._rates_version is version and runtime._refresher._rates is rates  # ... with the rates

    runtime.set_rates(None)  # and clearing them clears both holders
    assert runtime._refresher._rates is None and runtime._maintainer._rates is None
