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

"""Unit tests for AggregationBuilder terms order handling."""

from __future__ import annotations

from codemie.service.analytics.aggregation_builder import AggregationBuilder


def test_build_terms_agg_passes_list_order_through_unchanged():
    """A list of order dicts is emitted verbatim as terms.order (characterization)."""
    order = [{"unique_executions": "desc"}, {"_key": "asc"}]

    agg = AggregationBuilder.build_terms_agg(
        group_by_field="attributes.workflow_id.keyword",
        fetch_size=10,
        order=order,
        sub_aggs={},
    )

    assert agg["terms"]["order"] == [{"unique_executions": "desc"}, {"_key": "asc"}]


def test_build_terms_agg_still_passes_dict_order_through_unchanged():
    """The existing single-dict order form keeps its current behaviour."""
    agg = AggregationBuilder.build_terms_agg(
        group_by_field="attributes.project.keyword",
        fetch_size=10,
        order={"total_cost": "desc"},
        sub_aggs={},
    )

    assert agg["terms"]["order"] == {"total_cost": "desc"}
