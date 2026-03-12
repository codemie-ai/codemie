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

"""Unit tests for AIAdoptionConfig security validation.

Tests SQL injection prevention, range validation, and weight sum validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig


class TestSQLInjectionPrevention:
    """Test SQL injection pattern detection in config values."""

    def test_sql_terminator_rejected(self):
        """Test that semicolon (SQL terminator) is rejected."""
        with pytest.raises(ValidationError, match="SQL statement terminator"):
            AIAdoptionConfig(maturity_activation_threshold="20; DROP TABLE users;")

    def test_sql_comment_rejected(self):
        """Test that SQL comment syntax is rejected."""
        with pytest.raises(ValidationError, match="SQL comment"):
            AIAdoptionConfig(expertise_distribution_creator_activity_window="90--")

    def test_drop_table_rejected(self):
        """Test that DROP TABLE command is rejected."""
        with pytest.raises(ValidationError, match="DROP TABLE command"):
            AIAdoptionConfig(maturity_activation_threshold="1 DROP TABLE assistants")

    def test_delete_from_rejected(self):
        """Test that DELETE FROM command is rejected."""
        with pytest.raises(ValidationError, match="DELETE command"):
            AIAdoptionConfig(user_engagement_active_window_long="30 DELETE FROM users")

    def test_insert_into_rejected(self):
        """Test that INSERT INTO command is rejected."""
        with pytest.raises(ValidationError, match="INSERT command"):
            AIAdoptionConfig(asset_reusability_workflow_activation_threshold="5 INSERT INTO logs")

    def test_update_set_rejected(self):
        """Test that UPDATE SET command is rejected."""
        with pytest.raises(ValidationError, match="UPDATE command"):
            AIAdoptionConfig(minimum_users_threshold="5 UPDATE users SET")

    def test_union_select_rejected(self):
        """Test that UNION SELECT injection is rejected."""
        with pytest.raises(ValidationError, match="UNION SELECT injection"):
            AIAdoptionConfig(feature_adoption_conversation_depth_window="30 UNION SELECT password")

    def test_exec_rejected(self):
        """Test that EXEC command is rejected."""
        with pytest.raises(ValidationError, match="EXEC command"):
            AIAdoptionConfig(maturity_level_2_threshold="35 EXEC()")

    def test_xp_procedure_rejected(self):
        """Test that SQL Server extended procedures are rejected."""
        with pytest.raises(ValidationError, match="SQL Server extended procedure"):
            AIAdoptionConfig(user_engagement_multi_assistant_threshold="2 xp_cmdshell")

    def test_sp_procedure_rejected(self):
        """Test that SQL Server stored procedures are rejected."""
        with pytest.raises(ValidationError, match="SQL Server stored procedure"):
            AIAdoptionConfig(asset_reusability_team_adopted_threshold="2 sp_executesql")

    def test_single_quote_rejected(self):
        """Test that single quote (string delimiter) is rejected."""
        with pytest.raises(ValidationError, match="SQL statement terminator or string delimiter"):
            AIAdoptionConfig(maturity_activation_threshold="20'")

    def test_block_comment_start_rejected(self):
        """Test that SQL block comment start is rejected."""
        with pytest.raises(ValidationError, match="SQL block comment start"):
            AIAdoptionConfig(expertise_distribution_workflow_creator_bonus="0.5/*")

    def test_block_comment_end_rejected(self):
        """Test that SQL block comment end is rejected."""
        with pytest.raises(ValidationError, match="SQL block comment end"):
            AIAdoptionConfig(feature_adoption_complexity_weight="0.5*/")


class TestTypeValidation:
    """Test that incorrect types are rejected."""

    def test_string_instead_of_int_rejected(self):
        """Test that string values for int fields are type-coerced or rejected."""
        # Pydantic will try to coerce "20" to int, but SQL injection pattern will catch malicious strings
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_activation_threshold="malicious")

    def test_string_instead_of_float_rejected(self):
        """Test that invalid string values for float fields are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_activation_weight="malicious")

    def test_valid_string_numbers_coerced(self):
        """Test that valid numeric strings are coerced to proper types."""
        config = AIAdoptionConfig(maturity_activation_threshold="30")
        assert config.maturity_activation_threshold == 30
        assert isinstance(config.maturity_activation_threshold, int)

    def test_valid_float_string_coerced(self):
        """Test that valid float strings are coerced."""
        config = AIAdoptionConfig(
            user_engagement_activation_weight="0.35",
            user_engagement_dau_weight=0.15,
            user_engagement_mau_weight=0.20,
            user_engagement_engagement_distribution_weight=0.10,
            user_engagement_multi_assistant_weight=0.20,
            user_engagement_returning_user_weight=0.0,
        )
        assert config.user_engagement_activation_weight == 0.35
        assert isinstance(config.user_engagement_activation_weight, float)


class TestRangeValidation:
    """Test that values outside valid ranges are rejected."""

    def test_negative_threshold_rejected(self):
        """Test that negative thresholds are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_activation_threshold=-1)

    def test_zero_threshold_rejected(self):
        """Test that zero thresholds are rejected (minimum is 1)."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_activation_threshold=0)

    def test_threshold_too_large_rejected(self):
        """Test that thresholds exceeding maximum are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_activation_threshold=10000)

    def test_weight_negative_rejected(self):
        """Test that negative weights are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_activation_weight=-0.1)

    def test_weight_above_one_rejected(self):
        """Test that weights above 1.0 are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_activation_weight=1.5)

    def test_maturity_level_above_100_rejected(self):
        """Test that maturity levels above 100 are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_level_2_threshold=150)

    def test_percentile_above_one_rejected(self):
        """Test that percentiles above 1.0 are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(expertise_distribution_top_user_percentile=1.5)

    def test_percentile_zero_rejected(self):
        """Test that percentile of 0 is rejected (must be > 0)."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(expertise_distribution_top_user_percentile=0.0)

    def test_day_window_above_365_rejected(self):
        """Test that day windows above 365 are rejected."""
        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_active_window_long=400)


class TestWeightSumValidation:
    """Test that weight groups sum to 1.0."""

    def test_adoption_index_weights_must_sum_to_one(self):
        """Test that adoption index dimension weights must sum to 1.0."""
        with pytest.raises(ValidationError, match="Adoption index weights must sum to 1.0"):
            AIAdoptionConfig(
                adoption_index_user_engagement_weight=0.40,  # Sum = 1.10 (invalid)
                adoption_index_asset_reusability_weight=0.30,
                adoption_index_expertise_distribution_weight=0.20,
                adoption_index_feature_adoption_weight=0.20,
            )

    def test_d1_weights_must_sum_to_one(self):
        """Test that D1 component weights must sum to 1.0."""
        with pytest.raises(ValidationError, match="User Engagement weights must sum to 1.0"):
            AIAdoptionConfig(
                user_engagement_activation_weight=0.40,  # Sum = 1.10 (invalid)
                user_engagement_dau_weight=0.15,
                user_engagement_mau_weight=0.20,
                user_engagement_engagement_distribution_weight=0.15,
                user_engagement_multi_assistant_weight=0.20,
            )

    def test_d2_weights_must_sum_to_one(self):
        """Test that D2 component weights must sum to 1.0."""
        with pytest.raises(ValidationError, match="Asset Reusability weights must sum to 1.0"):
            AIAdoptionConfig(
                asset_reusability_team_adopted_weight=0.30,
                asset_reusability_active_assistants_weight=0.25,
                asset_reusability_workflow_reuse_weight=0.25,
                asset_reusability_workflow_exec_weight=0.10,
                asset_reusability_datasource_reuse_weight=0.20,  # Sum = 1.10 (invalid)
            )

    def test_d3_weights_must_sum_to_one(self):
        """Test that D3 component weights must sum to 1.0."""
        with pytest.raises(ValidationError, match="Expertise Distribution weights must sum to 1.0"):
            AIAdoptionConfig(
                expertise_distribution_concentration_weight=0.35,
                expertise_distribution_non_champion_weight=0.40,
                expertise_distribution_creator_diversity_weight=0.35,  # Sum = 1.10 (invalid)
            )

    def test_d4_weights_must_sum_to_one(self):
        """Test that D4 component weights must sum to 1.0."""
        with pytest.raises(ValidationError, match="Feature Adoption weights must sum to 1.0"):
            AIAdoptionConfig(
                feature_adoption_workflow_count_weight=0.30,
                feature_adoption_complexity_weight=0.50,
                feature_adoption_conversation_depth_weight=0.30,  # Sum = 1.10 (invalid)
            )

    def test_d4_complexity_sub_weights_must_sum_to_one(self):
        """Test that D4 complexity sub-weights must sum to 1.0."""
        with pytest.raises(ValidationError, match="Feature Adoption complexity sub-weights must sum to 1.0"):
            AIAdoptionConfig(
                feature_adoption_assistant_complexity_weight=0.70,  # Sum = 1.10 (invalid)
                feature_adoption_workflow_complexity_weight=0.40,
            )


class TestThresholdOrderingValidation:
    """Test that threshold values are in correct order."""

    def test_level_2_must_be_less_than_level_3(self):
        """Test that maturity level 2 threshold must be less than level 3."""
        with pytest.raises(ValidationError, match="must be less than maturity_level_3_threshold"):
            AIAdoptionConfig(
                maturity_level_2_threshold=70,
                maturity_level_3_threshold=60,
            )

    def test_level_2_equal_to_level_3_rejected(self):
        """Test that maturity level 2 equal to level 3 is rejected."""
        with pytest.raises(ValidationError, match="must be less than maturity_level_3_threshold"):
            AIAdoptionConfig(
                maturity_level_2_threshold=65,
                maturity_level_3_threshold=65,
            )

    def test_workflow_count_thresholds_must_be_ordered(self):
        """Test that workflow count thresholds must be in ascending order."""
        with pytest.raises(ValidationError, match="low_threshold must be <= medium_threshold"):
            AIAdoptionConfig(
                feature_adoption_workflow_count_low_threshold=10,
                feature_adoption_workflow_count_medium_threshold=5,
            )

        with pytest.raises(ValidationError, match="medium_threshold must be <= high_threshold"):
            AIAdoptionConfig(
                feature_adoption_workflow_count_medium_threshold=15,
                feature_adoption_workflow_count_high_threshold=10,
            )


class TestValidConfigCreation:
    """Test that valid configurations are accepted."""

    def test_default_config_is_valid(self):
        """Test that default config passes all validations."""
        config = AIAdoptionConfig()
        assert config.maturity_activation_threshold == 20
        assert config.adoption_index_user_engagement_weight == 0.30

    def test_custom_valid_config_accepted(self):
        """Test that custom valid config is accepted."""
        config = AIAdoptionConfig(
            maturity_activation_threshold=50,
            user_engagement_activation_weight=0.25,
            user_engagement_dau_weight=0.20,
            user_engagement_mau_weight=0.20,
            user_engagement_engagement_distribution_weight=0.15,
            user_engagement_multi_assistant_weight=0.20,
            user_engagement_returning_user_weight=0.0,
        )
        assert config.maturity_activation_threshold == 50
        assert config.user_engagement_activation_weight == 0.25

    def test_boundary_values_accepted(self):
        """Test that valid boundary values are accepted."""
        config = AIAdoptionConfig(
            maturity_activation_threshold=1,
            maturity_level_3_threshold=100,
        )
        assert config.maturity_activation_threshold == 1
        assert config.maturity_level_3_threshold == 100


class TestValidationOnAssignment:
    """Test that validation runs when fields are modified after creation."""

    def test_assignment_validation_runs(self):
        """Test that invalid assignment after creation is rejected."""
        config = AIAdoptionConfig()

        # Try to assign invalid value
        with pytest.raises(ValidationError):
            config.maturity_activation_threshold = -1

        # Original value should be unchanged
        assert config.maturity_activation_threshold == 20

    def test_valid_assignment_accepted(self):
        """Test that valid assignment after creation is accepted."""
        config = AIAdoptionConfig()
        config.maturity_activation_threshold = 30
        assert config.maturity_activation_threshold == 30


class TestDatabaseLoadingSimulation:
    """Test simulated database loading scenarios."""

    def test_load_from_dict_with_valid_data(self):
        """Test loading config from dict (simulating database row)."""
        db_data = {
            "maturity_activation_threshold": 25,
            "minimum_users_threshold": 10,
            "maturity_level_2_threshold": 40,
            "maturity_level_3_threshold": 70,
            # ... other fields use defaults
        }
        config = AIAdoptionConfig(**db_data)
        assert config.maturity_activation_threshold == 25
        assert config.minimum_users_threshold == 10

    def test_load_from_dict_with_sql_injection_blocked(self):
        """Test that SQL injection from database is blocked."""
        db_data = {
            "maturity_activation_threshold": "20; DROP TABLE users;",
        }
        with pytest.raises(ValidationError, match="SQL"):
            AIAdoptionConfig(**db_data)

    def test_load_from_dict_with_invalid_range_blocked(self):
        """Test that invalid range from database is blocked."""
        db_data = {
            "maturity_activation_threshold": 10000,
        }
        with pytest.raises(ValidationError):
            AIAdoptionConfig(**db_data)

    def test_load_from_dict_with_type_mismatch_blocked(self):
        """Test that type mismatch from database is blocked."""
        db_data = {
            "user_engagement_activation_weight": "not_a_number",
        }
        with pytest.raises(ValidationError):
            AIAdoptionConfig(**db_data)
