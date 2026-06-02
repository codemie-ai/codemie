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

"""Common query filters for analytics handlers."""

from __future__ import annotations

from codemie.service.analytics.metric_names import MetricName


def build_error_filtering_query(base_query: dict) -> dict:
    """Build query that filters error requests from metrics.

    Excludes:
    - CLI metrics with had_errors=true
    - LLM_PROXY_REQUESTS_TOTAL with response_status >= 400

    Other metrics are included without error filtering.

    Args:
        base_query: Base query to wrap with error filtering

    Returns:
        Modified query with error filtering applied
    """
    return {
        "bool": {
            "must": [base_query],
            "should": [
                {
                    "bool": {
                        "must_not": [
                            {"term": {"metric_name.keyword": MetricName.LLM_PROXY_REQUESTS_TOTAL.value}},
                        ],
                        "must": [
                            {
                                "bool": {
                                    "should": [
                                        {"term": {"attributes.had_errors": False}},
                                        {"bool": {"must_not": {"exists": {"field": "attributes.had_errors"}}}},
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ],
                    }
                },
                {
                    "bool": {
                        "must": [
                            {"term": {"metric_name.keyword": MetricName.LLM_PROXY_REQUESTS_TOTAL.value}},
                            {"range": {"attributes.response_status": {"lt": 400}}},
                        ]
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }
