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

"""Configuration model for AI Adoption Framework with Pydantic validation.

Centralizes all hardcoded parameters (weights, thresholds, time windows) used in
the adoption maturity calculations. This enables:
- Security: SQL injection prevention through strict validation
- Transparency: Parameters can be exposed in API responses
- Flexibility: Easy to adjust without SQL changes
- Validation: Ensures weights sum correctly and values are in valid ranges
- Testing: Isolated configuration for unit tests

SECURITY: Defense-in-Depth Approach
====================================
This module employs multiple layers of SQL injection protection:

1. **Layer 1: Pydantic Field Validator** (lines 244-288)
   - Runs BEFORE type coercion on all fields
   - Detects SQL injection patterns in string input
   - Blocks: semicolons, quotes, comments, SQL commands

2. **Layer 2: Type Validation**
   - Pydantic enforces strict types (int, float)
   - Invalid values raise ValidationError

3. **Layer 3: Explicit Casting in SQL Generation** (query_builder.py, etc.)
   - int() and float() casts used when interpolating into f-strings
   - Provides final defense if Pydantic has bugs or edge cases
   - Ensures value is pure Python number before SQL interpolation
   - Example: int(config.threshold) will fail if value is not numeric

Why We Keep Explicit Casting:
- Config can be loaded from API (untrusted input)
- Defense-in-depth: multiple validation layers
- Type guarantees: int()/float() ensure numeric types
- SQL safety: casts fail fast on non-numeric values
- Security best practice: never rely on single protection layer
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class AIAdoptionConfig(BaseModel):
    """Centralized configuration for AI Adoption Framework calculations.

    All parameters have default values matching the current production settings.
    Pydantic validators ensure:
    - Type safety (prevents SQL injection via wrong types)
    - Range validation (values within reasonable bounds)
    - Weight sum validation (dimension weights sum to 1.0)
    - SQL pattern detection (defense in depth)

    SECURITY CRITICAL: When loading from database, all values are validated
    before the config object is created. Invalid values raise ValidationError.
    """

    # =============================================================================
    # AI MATURITY (GENERIC)
    # =============================================================================

    # Shared activation threshold (used in D1 and D3)
    maturity_activation_threshold: int = Field(
        default=20,
        ge=1,
        le=1000,
        description="Minimum interactions for user activation",
    )

    # Time window for user activation (0 = all-time, backward compatible)
    user_engagement_activation_window: int = Field(
        default=90,
        ge=0,
        le=365,
        description="Time window in days for counting user activation (0 = all-time)",
    )

    # Minimum users for project inclusion in analytics
    minimum_users_threshold: int = Field(
        default=5,
        ge=1,
        le=100000,
        description="Minimum users for project inclusion",
    )

    # Maturity Level Thresholds
    maturity_level_2_threshold: int = Field(
        default=35,
        ge=0,
        le=100,
        description="Score threshold for L2: AUGMENTED",
    )

    maturity_level_3_threshold: int = Field(
        default=65,
        ge=0,
        le=100,
        description="Score threshold for L3: AGENTIC",
    )

    # Adoption Index Dimension Weights (must sum to 1.0)
    adoption_index_user_engagement_weight: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Weight of User Engagement in adoption index",
    )
    adoption_index_asset_reusability_weight: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Weight of Asset Reusability in adoption index",
    )
    adoption_index_expertise_distribution_weight: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Weight of Expertise Distribution in adoption index",
    )
    adoption_index_feature_adoption_weight: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Weight of Feature Adoption in adoption index",
    )

    # =============================================================================
    # USER ENGAGEMENT
    # =============================================================================

    # User Engagement Component Weights (must sum to 1.0)
    user_engagement_activation_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    user_engagement_dau_weight: float = Field(default=0.05, ge=0.0, le=1.0)
    user_engagement_mau_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    user_engagement_engagement_distribution_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    user_engagement_multi_assistant_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    user_engagement_returning_user_weight: float = Field(default=0.20, ge=0.0, le=1.0)

    # User Engagement Thresholds and Parameters
    user_engagement_multi_assistant_threshold: int = Field(default=2, ge=1, le=100)
    user_engagement_active_window_short: int = Field(default=7, ge=1, le=365, description="DAU window in days")
    user_engagement_active_window_long: int = Field(default=30, ge=1, le=365, description="MAU window in days")
    user_engagement_returning_user_window: int = Field(
        default=14,
        ge=0,
        le=365,
        description="Time window in days for returning user calculation. "
        "User must return within N days of first use to count as returning. "
        "Set to 0 for all-time mode (any return counts).",
    )

    # =============================================================================
    # ASSET REUSABILITY
    # =============================================================================

    # Asset Reusability Component Weights (must sum to 1.0)
    asset_reusability_team_adopted_weight: float = Field(default=0.30, ge=0.0, le=1.0)
    asset_reusability_active_assistants_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    asset_reusability_workflow_reuse_weight: float = Field(default=0.25, ge=0.0, le=1.0)
    asset_reusability_workflow_exec_weight: float = Field(default=0.10, ge=0.0, le=1.0)
    asset_reusability_datasource_reuse_weight: float = Field(default=0.10, ge=0.0, le=1.0)

    # Asset Reusability Thresholds and Parameters
    asset_reusability_team_adopted_threshold: int = Field(default=2, ge=1, le=100)
    asset_reusability_workflow_reuse_threshold: int = Field(default=2, ge=1, le=100)
    asset_reusability_workflow_activation_threshold: int = Field(default=5, ge=1, le=1000)

    # =============================================================================
    # EXPERTISE DISTRIBUTION
    # =============================================================================

    # Expertise Distribution Component Weights (must sum to 1.0)
    expertise_distribution_concentration_weight: float = Field(default=0.35, ge=0.0, le=1.0)
    expertise_distribution_non_champion_weight: float = Field(default=0.40, ge=0.0, le=1.0)
    expertise_distribution_creator_diversity_weight: float = Field(default=0.25, ge=0.0, le=1.0)

    # Expertise Distribution Thresholds and Parameters
    expertise_distribution_workflow_creator_bonus: float = Field(
        default=0.5,
        ge=0.0,
        le=2.0,
        description="Bonus multiplier (not part of sum validation)",
    )
    expertise_distribution_top_user_percentile: float = Field(
        default=0.2,
        gt=0.0,
        le=1.0,
        description="20% for top user concentration",
    )
    expertise_distribution_creator_activity_window: int = Field(
        default=90,
        ge=1,
        le=365,
        description="Creator tracking window in days",
    )

    # Expertise Distribution Scoring: Concentration
    expertise_distribution_concentration_critical_threshold: float = Field(default=80.0, ge=0.0, le=100.0)
    expertise_distribution_concentration_warning_threshold: float = Field(default=60.0, ge=0.0, le=100.0)
    expertise_distribution_concentration_healthy_upper: float = Field(default=60.0, ge=0.0, le=100.0)
    expertise_distribution_concentration_healthy_lower: float = Field(default=40.0, ge=0.0, le=100.0)
    expertise_distribution_concentration_flat_upper: float = Field(default=40.0, ge=0.0, le=100.0)
    expertise_distribution_concentration_flat_lower: float = Field(default=20.0, ge=0.0, le=100.0)
    expertise_distribution_concentration_critical_score: float = Field(default=0.2, ge=0.0, le=1.0)
    expertise_distribution_concentration_warning_score: float = Field(default=0.5, ge=0.0, le=1.0)
    expertise_distribution_concentration_healthy_score: float = Field(default=1.0, ge=0.0, le=1.0)
    expertise_distribution_concentration_flat_score: float = Field(default=0.8, ge=0.0, le=1.0)
    expertise_distribution_concentration_low_score: float = Field(default=0.6, ge=0.0, le=1.0)

    # Expertise Distribution Scoring: Non-Champion Activity
    expertise_distribution_non_champion_high_multiplier: float = Field(default=1.0, ge=0.0, le=2.0)
    expertise_distribution_non_champion_medium_multiplier: float = Field(default=0.5, ge=0.0, le=2.0)
    expertise_distribution_non_champion_low_multiplier: float = Field(default=0.2, ge=0.0, le=2.0)
    expertise_distribution_non_champion_high_score: float = Field(default=1.0, ge=0.0, le=1.0)
    expertise_distribution_non_champion_medium_score: float = Field(default=0.7, ge=0.0, le=1.0)
    expertise_distribution_non_champion_low_score: float = Field(default=0.4, ge=0.0, le=1.0)
    expertise_distribution_non_champion_minimal_score: float = Field(default=0.2, ge=0.0, le=1.0)

    # Expertise Distribution Scoring: Creator Diversity
    expertise_distribution_creator_diversity_high_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    expertise_distribution_creator_diversity_medium_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    expertise_distribution_creator_diversity_high_score: float = Field(default=1.0, ge=0.0, le=1.0)
    expertise_distribution_creator_diversity_medium_score: float = Field(default=0.6, ge=0.0, le=1.0)
    expertise_distribution_creator_diversity_low_score: float = Field(default=0.2, ge=0.0, le=1.0)

    # =============================================================================
    # FEATURE ADOPTION
    # =============================================================================

    # Feature Adoption Component Weights (must sum to 1.0)
    feature_adoption_workflow_count_weight: float = Field(default=0.30, ge=0.0, le=1.0)
    feature_adoption_complexity_weight: float = Field(default=0.50, ge=0.0, le=1.0)
    feature_adoption_conversation_depth_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    feature_adoption_assistant_complexity_weight: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Within complexity component",
    )
    feature_adoption_workflow_complexity_weight: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Within complexity component",
    )

    # Feature Adoption Thresholds and Parameters
    feature_adoption_conversation_depth_window: int = Field(
        default=30,
        ge=1,
        le=365,
        description="Analysis window in days",
    )
    feature_adoption_conversation_depth_normalizer: float = Field(default=10.0, gt=0.0, le=100.0)

    # Feature Adoption Complexity Level Weights
    feature_adoption_complexity_simple: float = Field(default=0.0, ge=0.0, le=1.0)
    feature_adoption_complexity_basic: float = Field(default=0.33, ge=0.0, le=1.0)
    feature_adoption_complexity_advanced: float = Field(default=0.67, ge=0.0, le=1.0)
    feature_adoption_complexity_complex: float = Field(default=1.0, ge=0.0, le=1.0)
    feature_adoption_complexity_multi_feature_bonus: float = Field(default=0.15, ge=0.0, le=1.0)

    # Feature Adoption Scoring: Workflow Count
    feature_adoption_workflow_count_low_threshold: int = Field(default=2, ge=0, le=1000)
    feature_adoption_workflow_count_medium_threshold: int = Field(default=5, ge=0, le=1000)
    feature_adoption_workflow_count_high_threshold: int = Field(default=10, ge=0, le=1000)
    feature_adoption_workflow_count_none_score: float = Field(default=0.2, ge=0.0, le=1.0)
    feature_adoption_workflow_count_low_score: float = Field(default=0.4, ge=0.0, le=1.0)
    feature_adoption_workflow_count_medium_score: float = Field(default=0.6, ge=0.0, le=1.0)
    feature_adoption_workflow_count_high_score: float = Field(default=0.8, ge=0.0, le=1.0)
    feature_adoption_workflow_count_very_high_score: float = Field(default=1.0, ge=0.0, le=1.0)

    # =============================================================================
    # FIELD VALIDATORS (Run on individual fields)
    # =============================================================================

    @field_validator("*", mode="before")
    @classmethod
    def check_sql_injection_patterns(cls, v: Any) -> Any:
        """Validate all fields for SQL injection patterns.

        SECURITY CRITICAL: This validator runs on EVERY field BEFORE type coercion
        to detect SQL injection attempts in raw input. Checks the string representation
        for dangerous SQL patterns.

        Args:
            v: Raw field value before type coercion

        Returns:
            The original value if safe

        Raises:
            ValueError: If SQL injection pattern detected
        """
        value_str = str(v)

        # SQL injection patterns (common attacks)
        dangerous_patterns = [
            (r"[;']", "SQL statement terminator or string delimiter"),
            (r"--", "SQL comment"),
            (r"/\*", "SQL block comment start"),
            (r"\*/", "SQL block comment end"),
            (r"\bDROP\s+TABLE\b", "DROP TABLE command"),
            (r"\bDELETE\s+FROM\b", "DELETE command"),
            (r"\bINSERT\s+INTO\b", "INSERT command"),
            (r"\bUPDATE\s+\w+\s+SET\b", "UPDATE command"),
            (r"\bUNION\s+(ALL\s+)?SELECT\b", "UNION SELECT injection"),
            (r"\bEXEC\s*\(", "EXEC command"),
            (r"\bxp_", "SQL Server extended procedure"),
            (r"\bsp_", "SQL Server stored procedure"),
        ]

        for pattern, description in dangerous_patterns:
            if re.search(pattern, value_str, re.IGNORECASE):
                raise ValueError(
                    f"Suspicious SQL pattern detected: {description}. "
                    f"Value: {value_str[:50]}... "
                    f"This may be an SQL injection attempt."
                )

        return v

    # =============================================================================
    # MODEL VALIDATORS (Run on entire model)
    # =============================================================================

    @model_validator(mode="after")
    def validate_weight_sums(self) -> AIAdoptionConfig:
        """Validate that all weight groups sum to 1.0.

        VALIDATION CRITICAL: Ensures mathematical correctness of scoring.
        All weight groups must sum to 1.0 (within tolerance of 0.01).

        Returns:
            Self if validation passes

        Raises:
            ValueError: If any weight group does not sum to 1.0
        """
        tolerance = 0.01

        # Adoption index dimension weights
        dimension_sum = (
            self.adoption_index_user_engagement_weight
            + self.adoption_index_asset_reusability_weight
            + self.adoption_index_expertise_distribution_weight
            + self.adoption_index_feature_adoption_weight
        )
        if abs(dimension_sum - 1.0) > tolerance:
            raise ValueError(f"Adoption index weights must sum to 1.0, got {dimension_sum:.4f}")

        # User Engagement component weights
        user_engagement_sum = (
            self.user_engagement_activation_weight
            + self.user_engagement_dau_weight
            + self.user_engagement_mau_weight
            + self.user_engagement_engagement_distribution_weight
            + self.user_engagement_multi_assistant_weight
            + self.user_engagement_returning_user_weight
        )
        if abs(user_engagement_sum - 1.0) > tolerance:
            raise ValueError(f"User Engagement weights must sum to 1.0, got {user_engagement_sum:.4f}")

        # Asset Reusability component weights
        asset_reusability_sum = (
            self.asset_reusability_team_adopted_weight
            + self.asset_reusability_active_assistants_weight
            + self.asset_reusability_workflow_reuse_weight
            + self.asset_reusability_workflow_exec_weight
            + self.asset_reusability_datasource_reuse_weight
        )
        if abs(asset_reusability_sum - 1.0) > tolerance:
            raise ValueError(f"Asset Reusability weights must sum to 1.0, got {asset_reusability_sum:.4f}")

        # Expertise Distribution component weights (excluding workflow_creator_bonus which is a multiplier)
        expertise_distribution_sum = (
            self.expertise_distribution_concentration_weight
            + self.expertise_distribution_non_champion_weight
            + self.expertise_distribution_creator_diversity_weight
        )
        if abs(expertise_distribution_sum - 1.0) > tolerance:
            raise ValueError(f"Expertise Distribution weights must sum to 1.0, got {expertise_distribution_sum:.4f}")

        # Feature Adoption component weights
        feature_adoption_sum = (
            self.feature_adoption_workflow_count_weight
            + self.feature_adoption_complexity_weight
            + self.feature_adoption_conversation_depth_weight
        )
        if abs(feature_adoption_sum - 1.0) > tolerance:
            raise ValueError(f"Feature Adoption weights must sum to 1.0, got {feature_adoption_sum:.4f}")

        # Feature Adoption complexity sub-weights
        feature_adoption_complexity_sum = (
            self.feature_adoption_assistant_complexity_weight + self.feature_adoption_workflow_complexity_weight
        )
        if abs(feature_adoption_complexity_sum - 1.0) > tolerance:
            raise ValueError(
                f"Feature Adoption complexity sub-weights must sum to 1.0, got {feature_adoption_complexity_sum:.4f}"
            )

        return self

    @model_validator(mode="after")
    def validate_threshold_ordering(self) -> AIAdoptionConfig:
        """Validate that thresholds are in correct order.

        Returns:
            Self if validation passes

        Raises:
            ValueError: If thresholds are not in correct order
        """
        if self.maturity_level_2_threshold >= self.maturity_level_3_threshold:
            raise ValueError(
                f"maturity_level_2_threshold ({self.maturity_level_2_threshold}) "
                f"must be less than maturity_level_3_threshold ({self.maturity_level_3_threshold})"
            )

        if self.feature_adoption_workflow_count_low_threshold > self.feature_adoption_workflow_count_medium_threshold:
            raise ValueError("low_threshold must be <= medium_threshold")

        if self.feature_adoption_workflow_count_medium_threshold > self.feature_adoption_workflow_count_high_threshold:
            raise ValueError("medium_threshold must be <= high_threshold")

        return self

    # =============================================================================
    # METHODS
    # =============================================================================

    def to_dict(self) -> dict:
        """Export all parameters for API response metadata.

        Returns:
            Dict with nested structure organizing parameters by category.
            Suitable for inclusion in metadata.calculation_parameters field.
        """
        return {
            "ai_maturity": {
                "activation_threshold": {
                    "value": self.maturity_activation_threshold,
                    "description": "Minimum number of interactions required for a user to be considered "
                    "'activated' across the framework. Used in D1 user activation and D3 "
                    "non-champion activity scoring.",
                },
                "minimum_users_threshold": {
                    "value": self.minimum_users_threshold,
                    "description": "Minimum number of users required for a project to be included in "
                    "analytics calculations. Projects below this threshold are excluded.",
                },
                "maturity_levels": {
                    "level_2_threshold": {
                        "value": self.maturity_level_2_threshold,
                        "description": "Minimum adoption index score (0-100) to reach L2: AUGMENTED maturity level.",
                    },
                    "level_3_threshold": {
                        "value": self.maturity_level_3_threshold,
                        "description": "Minimum adoption index score (0-100) to reach L3: AGENTIC maturity level.",
                    },
                },
                "adoption_index_weights": {
                    "user_engagement": {
                        "value": self.adoption_index_user_engagement_weight,
                        "description": "Weight of User Engagement in overall adoption index calculation.",
                    },
                    "asset_reusability": {
                        "value": self.adoption_index_asset_reusability_weight,
                        "description": "Weight of Asset Reusability in overall adoption index calculation.",
                    },
                    "expertise_distribution": {
                        "value": self.adoption_index_expertise_distribution_weight,
                        "description": "Weight of Expertise Distribution in overall adoption index calculation.",
                    },
                    "feature_adoption": {
                        "value": self.adoption_index_feature_adoption_weight,
                        "description": "Weight of Feature Adoption in overall adoption index calculation.",
                    },
                },
            },
            "user_engagement": {
                "component_weights": {
                    "activation": {
                        "value": self.user_engagement_activation_weight,
                        "description": "Weight of user activation rate in D1 score. Measures the percentage of "
                        "users who have reached the activation threshold (≥50 interactions). "
                        "Primary indicator of AI engagement.",
                    },
                    "dau": {
                        "value": self.user_engagement_dau_weight,
                        "description": "Weight of daily active users (DAU) in User Engagement score. "
                        "Measures the percentage of users active in the last 24 hours. "
                        "Provides real-time engagement pulse.",
                    },
                    "mau": {
                        "value": self.user_engagement_mau_weight,
                        "description": "Weight of monthly active users (MAU) in User Engagement score. "
                        "Measures the percentage of users active in the last 30 days. "
                        "Indicates consistent usage patterns.",
                    },
                    "engagement_distribution": {
                        "value": self.user_engagement_engagement_distribution_weight,
                        "description": "Weight of engagement distribution in User Engagement score. "
                        "Measures how evenly AI usage is spread across users (1 - stddev/mean). "
                        "Higher scores indicate more balanced usage distribution and healthier adoption.",
                    },
                    "multi_assistant": {
                        "value": self.user_engagement_multi_assistant_weight,
                        "description": "Weight of multi-assistant usage in User Engagement score. "
                        "Measures the percentage of users who interact with 2+ assistants. "
                        "Indicates exploration breadth and deeper AI tool adoption.",
                    },
                    "returning_user": {
                        "value": self.user_engagement_returning_user_weight,
                        "description": "Weight of returning user rate in User Engagement score. "
                        "Measures the percentage of users who return within the configured window "
                        "after first use. Indicates retention and habitual adoption.",
                    },
                },
                "parameters": {
                    "activation_window_days": {
                        "value": self.user_engagement_activation_window,
                        "description": "Time window in days for counting user activation. Users must reach the "
                        "activation threshold within this window to be considered activated. Set to 0 for "
                        "all-time activation (no time limit).",
                    },
                    "multi_assistant_threshold": {
                        "value": self.user_engagement_multi_assistant_threshold,
                        "description": "Minimum number of distinct assistants a user must interact with to be "
                        "considered a 'multi-assistant user'.",
                    },
                    "active_window_short_days": {
                        "value": self.user_engagement_active_window_short,
                        "description": "Time window in days for calculating weekly active users. Users "
                        "active within this window are considered 'weekly active'. "
                        "Note: DAU uses a fixed 24-hour window, not this parameter.",
                    },
                    "active_window_long_days": {
                        "value": self.user_engagement_active_window_long,
                        "description": "Time window in days for calculating Monthly Active Users (MAU). Users "
                        "active within this window are considered MAU.",
                    },
                    "returning_user_window_days": {
                        "value": self.user_engagement_returning_user_window,
                        "description": "Time window in days for returning user calculation. User must return "
                        "within N days of first use to count as returning. Set to 0 for all-time mode "
                        "(backward compatible - any return after first use counts as returning).",
                    },
                },
            },
            "asset_reusability": {
                "component_weights": {
                    "team_adopted": {
                        "value": self.asset_reusability_team_adopted_weight,
                        "description": "Weight of team-adopted assistants rate in Asset Reusability score. "
                        "Measures the percentage of assistants used by 2 or more users (team adoption).",
                    },
                    "active_assistants": {
                        "value": self.asset_reusability_active_assistants_weight,
                        "description": "Weight of active assistants utilization in Asset Reusability score. "
                        "Measures the percentage of assistants with sufficient usage (≥ activation threshold).",
                    },
                    "workflow_reuse": {
                        "value": self.asset_reusability_workflow_reuse_weight,
                        "description": "Weight of workflow reuse rate in Asset Reusability score. "
                        "Measures the percentage of workflows executed by 2 or more users.",
                    },
                    "workflow_execution": {
                        "value": self.asset_reusability_workflow_exec_weight,
                        "description": "Weight of workflow execution rate in Asset Reusability score. "
                        "Measures the percentage of workflows actively executed (≥ 10 executions in 30 days).",
                    },
                    "datasource_reuse": {
                        "value": self.asset_reusability_datasource_reuse_weight,
                        "description": "Weight of datasource reuse rate in Asset Reusability score. "
                        "Measures the percentage of datasources shared across 2 or more assistants.",
                    },
                },
                "parameters": {
                    "team_adopted_threshold": {
                        "value": self.asset_reusability_team_adopted_threshold,
                        "description": "Minimum number of distinct users required for an assistant to be "
                        "considered 'team-adopted'.",
                    },
                    "workflow_reuse_threshold": {
                        "value": self.asset_reusability_workflow_reuse_threshold,
                        "description": "Minimum number of distinct users required for a workflow to be "
                        "considered 'reused' by the team.",
                    },
                    "workflow_activation_threshold": {
                        "value": self.asset_reusability_workflow_activation_threshold,
                        "description": "Minimum number of executions in the last 30 days for a workflow to be "
                        "considered 'actively executed'.",
                    },
                },
            },
            "expertise_distribution": {
                "component_weights": {
                    "concentration": {
                        "value": self.expertise_distribution_concentration_weight,
                        "description": "Weight of user concentration metric in D3 score. Measures how evenly AI "
                        "usage is distributed (lower concentration = healthier ecosystem).",
                    },
                    "non_champion_activity": {
                        "value": self.expertise_distribution_non_champion_weight,
                        "description": "Weight of non-champion activity in D3 score. Measures activity level of "
                        "bottom 50% of users (higher activity = healthier ecosystem).",
                    },
                    "creator_diversity": {
                        "value": self.expertise_distribution_creator_diversity_weight,
                        "description": "Weight of creator diversity in D3 score. Measures the ratio of content "
                        "creators to total users (higher diversity = healthier ecosystem).",
                    },
                },
                "parameters": {
                    "workflow_creator_bonus": {
                        "value": self.expertise_distribution_workflow_creator_bonus,
                        "description": "Bonus multiplier applied to workflow creators when calculating creator "
                        "diversity. Workflow creators get additional weight as they demonstrate "
                        "higher AI expertise.",
                    },
                    "top_user_percentile": {
                        "value": self.expertise_distribution_top_user_percentile,
                        "description": "Top percentile of users used to calculate concentration. Concentration "
                        "measures what % of total usage comes from the top 20% of users.",
                    },
                    "creator_activity_window_days": {
                        "value": self.expertise_distribution_creator_activity_window,
                        "description": "Time window in days for tracking creator activity. Creators are counted "
                        "if they created assistants or workflows within this window.",
                    },
                },
                "scoring": {
                    "concentration": {
                        "critical_threshold": {
                            "value": self.expertise_distribution_concentration_critical_threshold,
                            "description": "If top 20% of users account for more than this % of total usage, "
                            "concentration is marked as CRITICAL. Indicates over-reliance on few "
                            "power users.",
                        },
                        "warning_threshold": {
                            "value": self.expertise_distribution_concentration_warning_threshold,
                            "description": "If top 20% of users account for more than this % of total usage, "
                            "concentration is marked as WARNING. Indicates moderate concentration "
                            "risk.",
                        },
                        "healthy_range": {
                            "value": [
                                self.expertise_distribution_concentration_healthy_lower,
                                self.expertise_distribution_concentration_healthy_upper,
                            ],
                            "description": "Concentration % range for HEALTHY status. HEALTHY = concentration "
                            "between 40-60%, indicating balanced usage distribution.",
                        },
                        "flat_range": {
                            "value": [
                                self.expertise_distribution_concentration_flat_lower,
                                self.expertise_distribution_concentration_flat_upper,
                            ],
                            "description": "Concentration % range for FLAT status. FLAT = concentration between "
                            "20-40%, indicating very flat/even distribution.",
                        },
                        "critical_score": {
                            "value": self.expertise_distribution_concentration_critical_score,
                            "description": "Score (0-1) assigned to CRITICAL concentration level. Low score "
                            "reflects unhealthy over-concentration.",
                        },
                        "warning_score": {
                            "value": self.expertise_distribution_concentration_warning_score,
                            "description": "Score (0-1) assigned to WARNING concentration level.",
                        },
                        "healthy_score": {
                            "value": self.expertise_distribution_concentration_healthy_score,
                            "description": "Score (0-1) assigned to HEALTHY concentration level. Optimal score "
                            "for balanced distribution.",
                        },
                        "flat_score": {
                            "value": self.expertise_distribution_concentration_flat_score,
                            "description": "Score (0-1) assigned to FLAT concentration level.",
                        },
                        "low_score": {
                            "value": self.expertise_distribution_concentration_low_score,
                            "description": "Score (0-1) for very low concentration (<20%).",
                        },
                    },
                    "non_champion_activity": {
                        "multipliers": {
                            "high": {
                                "value": self.expertise_distribution_non_champion_high_multiplier,
                                "description": "Multiplier of activation threshold for 'high' non-champion "
                                "activity. Bottom 50% median >= activation_threshold * 1.0 = high "
                                "activity.",
                            },
                            "medium": {
                                "value": self.expertise_distribution_non_champion_medium_multiplier,
                                "description": "Multiplier of activation threshold for 'medium' non-champion "
                                "activity. Bottom 50% median >= activation_threshold * 0.5 = "
                                "medium activity.",
                            },
                            "low": {
                                "value": self.expertise_distribution_non_champion_low_multiplier,
                                "description": "Multiplier of activation threshold for 'low' non-champion "
                                "activity. Bottom 50% median >= activation_threshold * 0.2 = low "
                                "activity.",
                            },
                        },
                        "scores": {
                            "high": {
                                "value": self.expertise_distribution_non_champion_high_score,
                                "description": "Score (0-1) for high non-champion activity. Optimal score when "
                                "bottom 50% are highly engaged.",
                            },
                            "medium": {
                                "value": self.expertise_distribution_non_champion_medium_score,
                                "description": "Score (0-1) for medium non-champion activity.",
                            },
                            "low": {
                                "value": self.expertise_distribution_non_champion_low_score,
                                "description": "Score (0-1) for low non-champion activity.",
                            },
                            "minimal": {
                                "value": self.expertise_distribution_non_champion_minimal_score,
                                "description": "Score (0-1) for minimal non-champion activity.",
                            },
                        },
                    },
                    "creator_diversity": {
                        "thresholds": {
                            "high": {
                                "value": self.expertise_distribution_creator_diversity_high_threshold,
                                "description": "Creator-to-user ratio threshold for 'high' diversity. If 15%+ of "
                                "users are creators, diversity is high.",
                            },
                            "medium": {
                                "value": self.expertise_distribution_creator_diversity_medium_threshold,
                                "description": "Creator-to-user ratio threshold for 'medium' diversity. If 5-15% "
                                "of users are creators, diversity is medium.",
                            },
                        },
                        "scores": {
                            "high": {
                                "value": self.expertise_distribution_creator_diversity_high_score,
                                "description": "Score (0-1) for high creator diversity. Optimal score when many "
                                "users are creating content.",
                            },
                            "medium": {
                                "value": self.expertise_distribution_creator_diversity_medium_score,
                                "description": "Score (0-1) for medium creator diversity.",
                            },
                            "low": {
                                "value": self.expertise_distribution_creator_diversity_low_score,
                                "description": "Score (0-1) for low creator diversity (<5%).",
                            },
                        },
                    },
                },
            },
            "feature_adoption": {
                "component_weights": {
                    "workflow_count": {
                        "value": self.feature_adoption_workflow_count_weight,
                        "description": "Weight of workflow count in D4 score. Measures organizational workflow "
                        "adoption based on total workflow count.",
                    },
                    "complexity": {
                        "value": self.feature_adoption_complexity_weight,
                        "description": "Weight of complexity-based feature utilization in D4 score. Combines "
                        "assistant and workflow complexity analysis.",
                    },
                    "conversation_depth": {
                        "value": self.feature_adoption_conversation_depth_weight,
                        "description": "Weight of conversation depth in D4 score. Measures median conversation "
                        "length (deeper conversations = more advanced usage).",
                    },
                    "assistant_complexity_ratio": {
                        "value": self.feature_adoption_assistant_complexity_weight,
                        "description": "Weight of assistant complexity within the complexity component. Assistant "
                        "complexity gets more weight than workflow complexity.",
                    },
                    "workflow_complexity_ratio": {
                        "value": self.feature_adoption_workflow_complexity_weight,
                        "description": "Weight of workflow complexity within the complexity component. Workflow "
                        "complexity gets less weight than assistant complexity.",
                    },
                },
                "parameters": {
                    "conversation_depth_window_days": {
                        "value": self.feature_adoption_conversation_depth_window,
                        "description": "Time window in days for calculating median conversation depth. Only "
                        "conversations within this window are analyzed.",
                    },
                    "conversation_depth_normalizer": {
                        "value": self.feature_adoption_conversation_depth_normalizer,
                        "description": "Normalization factor for conversation depth scoring. Depth score = "
                        "min(median_messages / 10.0, 1.0). A 10-message conversation = 100% score.",
                    },
                },
                "complexity_weights": {
                    "simple": {
                        "value": self.feature_adoption_complexity_simple,
                        "description": "Score weight (0-1) for simple complexity level. Simple = "
                        "assistants/workflows with no features (tools, datasources, MCP).",
                    },
                    "basic": {
                        "value": self.feature_adoption_complexity_basic,
                        "description": "Score weight (0-1) for basic complexity level. Basic = "
                        "assistants/workflows using one feature type.",
                    },
                    "advanced": {
                        "value": self.feature_adoption_complexity_advanced,
                        "description": "Score weight (0-1) for advanced complexity level. Advanced = "
                        "assistants/workflows using two feature types.",
                    },
                    "complex": {
                        "value": self.feature_adoption_complexity_complex,
                        "description": "Score weight (0-1) for complex complexity level. Complex = "
                        "assistants/workflows using all three feature types (tools + datasources + "
                        "MCP).",
                    },
                    "multi_feature_bonus": {
                        "value": self.feature_adoption_complexity_multi_feature_bonus,
                        "description": "Bonus score (0-1) for using multiple datasource types or multiple "
                        "assistants in workflows.",
                    },
                },
                "scoring": {
                    "workflow_count": {
                        "thresholds": {
                            "low": {
                                "value": self.feature_adoption_workflow_count_low_threshold,
                                "description": "Maximum workflow count to be considered 'low' adoption. Projects "
                                "with 1-2 workflows are in the low tier.",
                            },
                            "medium": {
                                "value": self.feature_adoption_workflow_count_medium_threshold,
                                "description": "Maximum workflow count to be considered 'medium' adoption. "
                                "Projects with 3-5 workflows are in the medium tier.",
                            },
                            "high": {
                                "value": self.feature_adoption_workflow_count_high_threshold,
                                "description": "Maximum workflow count to be considered 'high' adoption. Projects "
                                "with 6-10 workflows are in the high tier. 11+ = very high tier.",
                            },
                        },
                        "scores": {
                            "none": {
                                "value": self.feature_adoption_workflow_count_none_score,
                                "description": "Score (0-1) for projects with 0 workflows.",
                            },
                            "low": {
                                "value": self.feature_adoption_workflow_count_low_score,
                                "description": "Score (0-1) for low workflow count (1-2 workflows).",
                            },
                            "medium": {
                                "value": self.feature_adoption_workflow_count_medium_score,
                                "description": "Score (0-1) for medium workflow count (3-5 workflows).",
                            },
                            "high": {
                                "value": self.feature_adoption_workflow_count_high_score,
                                "description": "Score (0-1) for high workflow count (6-10 workflows).",
                            },
                            "very_high": {
                                "value": self.feature_adoption_workflow_count_very_high_score,
                                "description": "Score (0-1) for very high workflow count (11+ workflows).",
                            },
                        },
                    },
                },
            },
        }

    @staticmethod
    def _extract_value(nested_dict: dict, *keys: str) -> Any:
        """Navigate nested dict and extract 'value' field."""
        current = nested_dict
        for key in keys:
            if key not in current:
                return None
            current = current[key]
        if isinstance(current, dict) and "value" in current:
            return current["value"]
        return current

    @classmethod
    def _parse_ai_maturity(cls, data: dict, flat_dict: dict) -> None:
        """Parse AI maturity section."""
        if "ai_maturity" not in data:
            return
        am = data["ai_maturity"]
        flat_dict["maturity_activation_threshold"] = cls._extract_value(am, "activation_threshold")
        flat_dict["minimum_users_threshold"] = cls._extract_value(am, "minimum_users_threshold")
        flat_dict["maturity_level_2_threshold"] = cls._extract_value(am, "maturity_levels", "level_2_threshold")
        flat_dict["maturity_level_3_threshold"] = cls._extract_value(am, "maturity_levels", "level_3_threshold")
        flat_dict["adoption_index_user_engagement_weight"] = cls._extract_value(
            am, "adoption_index_weights", "user_engagement"
        )
        flat_dict["adoption_index_asset_reusability_weight"] = cls._extract_value(
            am, "adoption_index_weights", "asset_reusability"
        )
        flat_dict["adoption_index_expertise_distribution_weight"] = cls._extract_value(
            am, "adoption_index_weights", "expertise_distribution"
        )
        flat_dict["adoption_index_feature_adoption_weight"] = cls._extract_value(
            am, "adoption_index_weights", "feature_adoption"
        )

    @classmethod
    def _parse_user_engagement(cls, data: dict, flat_dict: dict) -> None:
        """Parse user engagement section."""
        if "user_engagement" not in data:
            return
        ue = data["user_engagement"]
        flat_dict["user_engagement_activation_weight"] = cls._extract_value(ue, "component_weights", "activation")
        flat_dict["user_engagement_dau_weight"] = cls._extract_value(ue, "component_weights", "dau")
        flat_dict["user_engagement_mau_weight"] = cls._extract_value(ue, "component_weights", "mau")
        flat_dict["user_engagement_engagement_distribution_weight"] = cls._extract_value(
            ue, "component_weights", "engagement_distribution"
        )
        flat_dict["user_engagement_multi_assistant_weight"] = cls._extract_value(
            ue, "component_weights", "multi_assistant"
        )
        flat_dict["user_engagement_returning_user_weight"] = cls._extract_value(
            ue, "component_weights", "returning_user"
        )
        flat_dict["user_engagement_multi_assistant_threshold"] = cls._extract_value(
            ue, "parameters", "multi_assistant_threshold"
        )
        flat_dict["user_engagement_active_window_short"] = cls._extract_value(
            ue, "parameters", "active_window_short_days"
        )
        flat_dict["user_engagement_active_window_long"] = cls._extract_value(
            ue, "parameters", "active_window_long_days"
        )
        flat_dict["user_engagement_activation_window"] = cls._extract_value(ue, "parameters", "activation_window_days")
        flat_dict["user_engagement_returning_user_window"] = cls._extract_value(
            ue, "parameters", "returning_user_window_days"
        )

    @classmethod
    def _parse_asset_reusability(cls, data: dict, flat_dict: dict) -> None:
        """Parse asset reusability section."""
        if "asset_reusability" not in data:
            return
        ar = data["asset_reusability"]
        flat_dict["asset_reusability_team_adopted_weight"] = cls._extract_value(ar, "component_weights", "team_adopted")
        flat_dict["asset_reusability_active_assistants_weight"] = cls._extract_value(
            ar, "component_weights", "active_assistants"
        )
        flat_dict["asset_reusability_workflow_reuse_weight"] = cls._extract_value(
            ar, "component_weights", "workflow_reuse"
        )
        flat_dict["asset_reusability_workflow_exec_weight"] = cls._extract_value(
            ar, "component_weights", "workflow_execution"
        )
        flat_dict["asset_reusability_datasource_reuse_weight"] = cls._extract_value(
            ar, "component_weights", "datasource_reuse"
        )
        flat_dict["asset_reusability_team_adopted_threshold"] = cls._extract_value(
            ar, "parameters", "team_adopted_threshold"
        )
        flat_dict["asset_reusability_workflow_reuse_threshold"] = cls._extract_value(
            ar, "parameters", "workflow_reuse_threshold"
        )
        flat_dict["asset_reusability_workflow_activation_threshold"] = cls._extract_value(
            ar, "parameters", "workflow_activation_threshold"
        )

    @classmethod
    def _parse_expertise_distribution(cls, data: dict, flat_dict: dict) -> None:
        """Parse expertise distribution section."""
        if "expertise_distribution" not in data:
            return
        ed = data["expertise_distribution"]
        flat_dict["expertise_distribution_concentration_weight"] = cls._extract_value(
            ed, "component_weights", "concentration"
        )
        flat_dict["expertise_distribution_non_champion_weight"] = cls._extract_value(
            ed, "component_weights", "non_champion_activity"
        )
        flat_dict["expertise_distribution_creator_diversity_weight"] = cls._extract_value(
            ed, "component_weights", "creator_diversity"
        )
        flat_dict["expertise_distribution_workflow_creator_bonus"] = cls._extract_value(
            ed, "parameters", "workflow_creator_bonus"
        )
        flat_dict["expertise_distribution_top_user_percentile"] = cls._extract_value(
            ed, "parameters", "top_user_percentile"
        )
        flat_dict["expertise_distribution_creator_activity_window"] = cls._extract_value(
            ed, "parameters", "creator_activity_window_days"
        )

        if "scoring" not in ed:
            return
        scoring = ed["scoring"]

        # Parse scoring subsections using helper methods
        cls._parse_concentration_scoring(scoring, flat_dict)
        cls._parse_non_champion_activity_scoring(scoring, flat_dict)
        cls._parse_creator_diversity_scoring(scoring, flat_dict)

    @classmethod
    def _parse_concentration_scoring(cls, scoring: dict, flat_dict: dict) -> None:
        """Parse concentration scoring subsection."""
        if "concentration" not in scoring:
            return
        conc = scoring["concentration"]
        flat_dict["expertise_distribution_concentration_critical_threshold"] = cls._extract_value(
            conc, "critical_threshold"
        )
        flat_dict["expertise_distribution_concentration_warning_threshold"] = cls._extract_value(
            conc, "warning_threshold"
        )
        healthy_range = cls._extract_value(conc, "healthy_range")
        if isinstance(healthy_range, list) and len(healthy_range) == 2:
            flat_dict["expertise_distribution_concentration_healthy_lower"] = healthy_range[0]
            flat_dict["expertise_distribution_concentration_healthy_upper"] = healthy_range[1]
        flat_dict["expertise_distribution_concentration_critical_score"] = cls._extract_value(conc, "critical_score")
        flat_dict["expertise_distribution_concentration_warning_score"] = cls._extract_value(conc, "warning_score")
        flat_dict["expertise_distribution_concentration_healthy_score"] = cls._extract_value(conc, "healthy_score")
        flat_dict["expertise_distribution_concentration_flat_score"] = cls._extract_value(conc, "flat_score")
        flat_dict["expertise_distribution_concentration_low_score"] = cls._extract_value(conc, "low_score")
        flat_range = cls._extract_value(conc, "flat_range")
        if isinstance(flat_range, list) and len(flat_range) == 2:
            flat_dict["expertise_distribution_concentration_flat_lower"] = flat_range[0]
            flat_dict["expertise_distribution_concentration_flat_upper"] = flat_range[1]

    @classmethod
    def _parse_non_champion_activity_scoring(cls, scoring: dict, flat_dict: dict) -> None:
        """Parse non-champion activity scoring subsection."""
        if "non_champion_activity" not in scoring:
            return
        nca = scoring["non_champion_activity"]
        if "multipliers" in nca:
            mult = nca["multipliers"]
            flat_dict["expertise_distribution_non_champion_high_multiplier"] = cls._extract_value(mult, "high")
            flat_dict["expertise_distribution_non_champion_medium_multiplier"] = cls._extract_value(mult, "medium")
            flat_dict["expertise_distribution_non_champion_low_multiplier"] = cls._extract_value(mult, "low")
        if "scores" in nca:
            scores = nca["scores"]
            flat_dict["expertise_distribution_non_champion_high_score"] = cls._extract_value(scores, "high")
            flat_dict["expertise_distribution_non_champion_medium_score"] = cls._extract_value(scores, "medium")
            flat_dict["expertise_distribution_non_champion_low_score"] = cls._extract_value(scores, "low")
            flat_dict["expertise_distribution_non_champion_minimal_score"] = cls._extract_value(scores, "minimal")

    @classmethod
    def _parse_creator_diversity_scoring(cls, scoring: dict, flat_dict: dict) -> None:
        """Parse creator diversity scoring subsection."""
        if "creator_diversity" not in scoring:
            return
        cd = scoring["creator_diversity"]
        if "thresholds" in cd:
            thresh = cd["thresholds"]
            flat_dict["expertise_distribution_creator_diversity_high_threshold"] = cls._extract_value(thresh, "high")
            flat_dict["expertise_distribution_creator_diversity_medium_threshold"] = cls._extract_value(
                thresh, "medium"
            )
        if "scores" in cd:
            scores = cd["scores"]
            flat_dict["expertise_distribution_creator_diversity_high_score"] = cls._extract_value(scores, "high")
            flat_dict["expertise_distribution_creator_diversity_medium_score"] = cls._extract_value(scores, "medium")
            flat_dict["expertise_distribution_creator_diversity_low_score"] = cls._extract_value(scores, "low")

    @classmethod
    def _parse_feature_adoption(cls, data: dict, flat_dict: dict) -> None:
        """Parse feature adoption section."""
        if "feature_adoption" not in data:
            return
        fa = data["feature_adoption"]
        flat_dict["feature_adoption_workflow_count_weight"] = cls._extract_value(
            fa, "component_weights", "workflow_count"
        )
        flat_dict["feature_adoption_complexity_weight"] = cls._extract_value(fa, "component_weights", "complexity")
        flat_dict["feature_adoption_conversation_depth_weight"] = cls._extract_value(
            fa, "component_weights", "conversation_depth"
        )
        flat_dict["feature_adoption_assistant_complexity_weight"] = cls._extract_value(
            fa, "component_weights", "assistant_complexity_ratio"
        )
        flat_dict["feature_adoption_workflow_complexity_weight"] = cls._extract_value(
            fa, "component_weights", "workflow_complexity_ratio"
        )
        flat_dict["feature_adoption_conversation_depth_window"] = cls._extract_value(
            fa, "parameters", "conversation_depth_window_days"
        )
        flat_dict["feature_adoption_conversation_depth_normalizer"] = cls._extract_value(
            fa, "parameters", "conversation_depth_normalizer"
        )

        # Complexity weights
        if "complexity_weights" in fa:
            cw = fa["complexity_weights"]
            flat_dict["feature_adoption_complexity_simple"] = cls._extract_value(cw, "simple")
            flat_dict["feature_adoption_complexity_basic"] = cls._extract_value(cw, "basic")
            flat_dict["feature_adoption_complexity_advanced"] = cls._extract_value(cw, "advanced")
            flat_dict["feature_adoption_complexity_complex"] = cls._extract_value(cw, "complex")
            flat_dict["feature_adoption_complexity_multi_feature_bonus"] = cls._extract_value(cw, "multi_feature_bonus")

        # Scoring
        if "scoring" in fa and "workflow_count" in fa["scoring"]:
            wc = fa["scoring"]["workflow_count"]
            if "thresholds" in wc:
                thresh = wc["thresholds"]
                flat_dict["feature_adoption_workflow_count_low_threshold"] = cls._extract_value(thresh, "low")
                flat_dict["feature_adoption_workflow_count_medium_threshold"] = cls._extract_value(thresh, "medium")
                flat_dict["feature_adoption_workflow_count_high_threshold"] = cls._extract_value(thresh, "high")
            if "scores" in wc:
                scores = wc["scores"]
                flat_dict["feature_adoption_workflow_count_none_score"] = cls._extract_value(scores, "none")
                flat_dict["feature_adoption_workflow_count_low_score"] = cls._extract_value(scores, "low")
                flat_dict["feature_adoption_workflow_count_medium_score"] = cls._extract_value(scores, "medium")
                flat_dict["feature_adoption_workflow_count_high_score"] = cls._extract_value(scores, "high")
                flat_dict["feature_adoption_workflow_count_very_high_score"] = cls._extract_value(scores, "very_high")

    @classmethod
    def from_dict(cls, data: dict) -> AIAdoptionConfig:
        """Parse nested dict structure (from frontend localStorage) into flat Pydantic model.

        Frontend stores config in nested format with value/description objects:
        {"ai_maturity": {"activation_threshold": {"value": 100, "description": "..."}}}

        Backend Pydantic model uses flat field names:
        maturity_activation_threshold: int = 20

        This method flattens the nested structure to create a valid config object.

        Args:
            data: Nested dict from frontend (from to_dict() format)

        Returns:
            AIAdoptionConfig instance with values extracted from nested structure
        """
        # Build flat dict for Pydantic model
        flat_dict: dict[str, Any] = {}

        # Parse each section using helper methods
        cls._parse_ai_maturity(data, flat_dict)
        cls._parse_user_engagement(data, flat_dict)
        cls._parse_asset_reusability(data, flat_dict)
        cls._parse_expertise_distribution(data, flat_dict)
        cls._parse_feature_adoption(data, flat_dict)

        # Remove None values and create instance
        flat_dict = {k: v for k, v in flat_dict.items() if v is not None}
        return cls(**flat_dict)

    model_config = {
        "validate_assignment": True,  # Validate on field assignment after creation
        "str_strip_whitespace": True,  # Strip whitespace from strings
        "use_enum_values": True,  # Use enum values in dict/json
    }
