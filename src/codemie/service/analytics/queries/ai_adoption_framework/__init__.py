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

"""AI Adoption Framework - Modular Query Components.

This package provides a modular, configurable architecture for the AI Adoption Maturity Framework.
Instead of a monolithic 830-line query, it breaks down into reusable components:

- config.py: Configuration dataclass with all parameters (weights, thresholds, time windows)
- base_queries.py: Foundation CTEs (params, assistant_stats, workflow_stats, user_stats, creator_activity)
- dimension_queries.py: Dimension-specific CTEs (D1-D4, 13 functions total)
- score_expressions.py: Reusable SQL expression builders for dimension scoring and complexity calculations
- composite_queries.py: Scoring logic (composite_scores CTE with dynamic weights)
- query_builder.py: Query composition orchestrator (AdoptionQueryBuilder class)
- column_definitions.py: Centralized column metadata for API responses

Benefits:
- Transparency: Calculation parameters exposed in API responses
- Flexibility: Easy to adjust weights and thresholds without SQL changes
- Testability: Each component can be tested independently
- Performance: Endpoint-specific queries load only needed CTEs
- Maintainability: Score expressions eliminate 340+ lines of duplicated formulas
"""

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig
from codemie.service.analytics.queries.ai_adoption_framework.query_builder import (
    build_maturity_query,
    build_dimensions_query,
    build_user_engagement_metrics_query,
    build_asset_reusability_metrics_query,
    build_expertise_distribution_metrics_query,
    build_feature_adoption_metrics_query,
    build_overview_query,
    build_project_count_query,
)
from codemie.service.analytics.queries.ai_adoption_framework import query_builder

__all__ = [
    "AIAdoptionConfig",
    "query_builder",  # Module export for dynamic function access
    "build_maturity_query",
    "build_dimensions_query",
    "build_user_engagement_metrics_query",
    "build_asset_reusability_metrics_query",
    "build_expertise_distribution_metrics_query",
    "build_feature_adoption_metrics_query",
    "build_overview_query",
    "build_project_count_query",
]
