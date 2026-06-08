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

"""Helper for building terms aggregations with fetch-and-slice pagination.

This module provides utilities for building Elasticsearch terms aggregations
with stateless pagination support and cardinality-based total counts.
"""

from __future__ import annotations

from typing import Any


class AggregationBuilder:
    """Builds terms aggregations for stateless pagination with sorting."""

    @staticmethod
    def build_terms_agg(
        group_by_field: str,
        fetch_size: int,
        order: dict[str, str],
        sub_aggs: dict[str, Any],
        script: str | None = None,
    ) -> dict[str, Any]:
        """Build terms aggregation structure with sorting support.

        Args:
            group_by_field: Field to group by (e.g., "attributes.project.keyword")
            fetch_size: Number of buckets to fetch (page+1) * per_page
            order: Sort order dict (e.g., {"total_cost": "desc"})
            sub_aggs: Sub-aggregations (metrics) to include
            script: Optional Painless script source; when provided, replaces the field-based
                grouping (e.g. to normalise bucket keys before aggregation)

        Returns:
            Terms aggregation structure

        Raises:
            ValueError: If fetch_size exceeds Elasticsearch limits (10,000)
        """
        # Safety check for deep pagination
        if fetch_size > 10000:
            raise ValueError(
                f"Pagination limit exceeded. Requested {fetch_size} items, max is 10000. "
                "Please refine filters to reduce result set size."
            )

        group_by: dict[str, Any] = (
            {"script": {"source": script, "lang": "painless"}} if script else {"field": group_by_field}
        )

        return {
            "terms": {
                **group_by,
                "size": fetch_size,
                "order": order,
            },
            "aggs": sub_aggs,
        }

    @staticmethod
    def add_cardinality_for_total(
        agg_body: dict[str, Any],
        field: str,
        agg_name: str = "total_buckets",
    ) -> dict[str, Any]:
        """Add sibling cardinality aggregation for accurate total_count.

        Args:
            agg_body: Aggregation body to modify
            field: Field to count unique values
            agg_name: Name for cardinality aggregation

        Returns:
            Modified aggregation body with sibling cardinality
        """
        agg_body["aggs"][agg_name] = {
            "cardinality": {
                "field": field,
                "precision_threshold": 40000,
            }
        }
        return agg_body

    @staticmethod
    def inject_global_totals(agg_body: dict[str, Any], totals_aggs: dict[str, dict]) -> dict[str, Any]:
        """Inject global metric aggregations as siblings alongside paginated_results.

        Adds top-level metric aggs (sum, filter+sum, etc.) that ES evaluates in a single
        document pass — no per-bucket sorting overhead, unlike the terms agg.

        Args:
            agg_body: Aggregation body to modify (must have "aggs" key)
            totals_aggs: Mapping of column_id → ES aggregation spec

        Returns:
            Modified aggregation body with global totals aggs injected
        """
        agg_body["aggs"].update(totals_aggs)
        return agg_body

    @staticmethod
    def extract_global_totals(result: dict[str, Any], totals_aggs: dict[str, dict]) -> dict[str, float]:
        """Extract global metric totals from aggregation result.

        Handles three agg shapes:
        - Direct metric (sum, cardinality, avg): ``{"sum": {...}}``
          → ``result[col_id]["value"]``
        - Filter with nested metric: ``{"filter": ..., "aggs": {"<sub>": ...}}``
          → ``result[col_id]["<sub>"]["value"]``
        - Plain filter (no nested agg): ``{"filter": ...}``
          → ``result[col_id]["doc_count"]``

        Args:
            result: Elasticsearch aggregation result
            totals_aggs: Same mapping passed to inject_global_totals

        Returns:
            Dict mapping column_id to total value (rounded to 2 decimals)
        """
        aggregations = result.get("aggregations", {})
        totals: dict[str, float] = {}
        for col_id, agg_spec in totals_aggs.items():
            agg_result = aggregations.get(col_id, {})
            if "filter" in agg_spec:
                sub_aggs = agg_spec.get("aggs", {})
                if sub_aggs:
                    sub_key = next(iter(sub_aggs))
                    totals[col_id] = round(agg_result.get(sub_key, {}).get("value", 0.0), 2)
                else:
                    totals[col_id] = float(agg_result.get("doc_count", 0))
            else:
                totals[col_id] = round(agg_result.get("value", 0.0), 2)
        return totals

    @staticmethod
    def slice_buckets_for_page(
        buckets: list[dict[str, Any]],
        page: int,
        per_page: int,
    ) -> list[dict[str, Any]]:
        """Slice fetched buckets to get current page data.

        Args:
            buckets: All fetched buckets from terms aggregation (pre-sorted)
            page: Current page number (0-indexed)
            per_page: Items per page

        Returns:
            Sliced buckets for current page (maintains sort order)
        """
        start_idx = page * per_page
        end_idx = (page + 1) * per_page
        return buckets[start_idx:end_idx]

    @staticmethod
    def build_zero_token_filter_aggs() -> dict[str, Any]:
        """Build bucket aggregations for filtering zero-token entries.

        Creates filter aggregations to count tokens from web (assistant/workflow)
        and CLI sources, plus a bucket_selector to filter out entries with zero
        total tokens. This ensures consistent filtering across Activity and
        Spending endpoints.

        CLI tokens use the NEW CLI_LLM_USAGE_TOTAL metric (server-side tracked from
        LiteLLM proxy) for accuracy, matching the CLI spending calculations.

        Returns:
            Dict of aggregations: 3-bucket (web input), 4-bucket (web output),
            5-bucket (CLI input), 6-bucket (CLI output), filter_zero_tokens
        """
        from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD
        from codemie.service.analytics.metric_names import MetricName

        return {
            # LLM input tokens (assistant + workflow, excludes embeddings)
            "3-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "terms": {
                                    METRIC_NAME_KEYWORD_FIELD: [
                                        MetricName.CONVERSATION_ASSISTANT_USAGE.value,
                                        MetricName.WORKFLOW_EXECUTION_TOTAL.value,
                                    ]
                                }
                            }
                        ],
                    }
                },
                "aggs": {
                    "3-metric": {
                        "sum": {"field": "attributes.input_tokens"},
                    },
                },
            },
            # LLM output tokens (assistant + workflow, excludes embeddings)
            "4-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "terms": {
                                    METRIC_NAME_KEYWORD_FIELD: [
                                        MetricName.CONVERSATION_ASSISTANT_USAGE.value,
                                        MetricName.WORKFLOW_EXECUTION_TOTAL.value,
                                    ]
                                }
                            }
                        ],
                    }
                },
                "aggs": {
                    "4-metric": {
                        "sum": {"field": "attributes.output_tokens"},
                    },
                },
            },
            # CLI input tokens (use NEW LiteLLM proxy metric for accurate server-side tracking)
            "5-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            {"term": {"attributes.cli_request": True}},
                        ],
                    }
                },
                "aggs": {
                    "5-metric": {
                        "sum": {"field": "attributes.input_tokens"},
                    },
                },
            },
            # CLI output tokens (use NEW LiteLLM proxy metric for accurate server-side tracking)
            "6-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            {"term": {"attributes.cli_request": True}},
                        ],
                    }
                },
                "aggs": {
                    "6-metric": {
                        "sum": {"field": "attributes.output_tokens"},
                    },
                },
            },
            # Filter out buckets with zero total tokens (web + CLI)
            "filter_zero_tokens": {
                "bucket_selector": {
                    "buckets_path": {
                        "webInput": "3-bucket>3-metric",
                        "webOutput": "4-bucket>4-metric",
                        "cliInput": "5-bucket>5-metric",
                        "cliOutput": "6-bucket>6-metric",
                    },
                    "script": "(params.webInput + params.webOutput + params.cliInput + params.cliOutput) > 0",
                }
            },
        }
