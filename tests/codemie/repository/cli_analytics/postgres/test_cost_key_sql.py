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

"""The pricing builders of the cost key are public and the funding routes have one definition (EPMCDME-17051, T1)."""

from __future__ import annotations

import hashlib

from codemie.repository.cli_analytics import scope
from codemie.repository.cli_analytics.postgres import cost_key_sql
from codemie.rest_api.routers import cli_analytics_ingest_route
from codemie.service.analytics.handlers import funding_buckets

# sha256 of the statements as the builders produce them on the base (EPMCDME-16427 part2).
_SESSION_SQL_DIGEST = "".join(("afbf6510e1db9cc4a1ace574a68038da", "917c86ac3d3699e50305ce4292935feb"))
_SPEND_SQL_DIGEST = "".join(("d9b89f9b21d02187546c594a6d0a9404", "babd326a6220e384de85a63c8d852f0c"))
_GROUPS_SQL_DIGEST = "".join(("fbde8947bbc1dd3a0dbd08bdf5b90e18", "2fdd32c64a920c243a0f6ff115d4bf51"))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_public_pricing_builders_keep_session_cost_key_unchanged():
    assert _digest(cost_key_sql.session_cost_key_sql()) == _SESSION_SQL_DIGEST
    assert _digest(cost_key_sql.session_cost_key_sql(True)) == _SPEND_SQL_DIGEST
    assert _digest(cost_key_sql.cost_key_from_groups_sql()) == _GROUPS_SQL_DIGEST
    assert cost_key_sql.cost_terms() in cost_key_sql.session_cost_key_sql()
    assert cost_key_sql.RATES_JOIN in cost_key_sql.session_cost_key_sql()
    assert cost_key_sql.cache_read_term() in cost_key_sql.cost_terms()
    assert "r.cache_read" in cost_key_sql.cache_read_term()
    assert "r.output" not in cost_key_sql.cache_read_term()
    unpriced = cost_key_sql.unpriced_test()
    for column in cost_key_sql.RATE_COLUMNS:
        assert f"r.{column} IS NULL" in unpriced

    assert scope.FUNDING_ROUTES == ("api_based", "subscription", "unknown")
    assert funding_buckets.FUNDING_ROUTES is scope.FUNDING_ROUTES
    assert set(scope.FUNDING_ROUTES) == cli_analytics_ingest_route._FUNDING_VALUES
