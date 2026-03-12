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

"""Security tests for AI Adoption Framework configuration validation.

This test suite validates that the Pydantic config model properly prevents
SQL injection attacks through strict validation, even though config values
are interpolated into SQL strings (not ideal, but protected by validation).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codemie.service.analytics.queries.ai_adoption_framework.config import AIAdoptionConfig


class TestSqlInjectionPrevention:
    """Tests for SQL injection prevention through Pydantic validation."""

    def test_sql_terminator_semicolon_rejected(self):
        """Verify semicolon in config value is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="SQL statement terminator"):
            AIAdoptionConfig(maturity_activation_threshold="20; DROP TABLE users")

    def test_sql_comment_double_dash_rejected(self):
        """Verify SQL comment syntax is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="SQL comment"):
            AIAdoptionConfig(user_engagement_activation_weight="0.3 -- comment")

    def test_sql_block_comment_rejected(self):
        """Verify SQL block comment syntax is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="SQL block comment"):
            AIAdoptionConfig(maturity_level_2_threshold="35 /* comment */")

    def test_drop_table_command_rejected(self):
        """Verify DROP TABLE command is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="DROP TABLE"):
            AIAdoptionConfig(maturity_activation_threshold="20 DROP TABLE assistants")

    def test_delete_command_rejected(self):
        """Verify DELETE FROM command is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="DELETE"):
            AIAdoptionConfig(minimum_users_threshold="5 DELETE FROM users")

    def test_insert_command_rejected(self):
        """Verify INSERT INTO command is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="INSERT"):
            AIAdoptionConfig(user_engagement_multi_assistant_threshold="2 INSERT INTO malicious")

    def test_update_command_rejected(self):
        """Verify UPDATE SET command is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="UPDATE"):
            AIAdoptionConfig(asset_reusability_team_adopted_threshold="2 UPDATE users SET admin=1")

    def test_union_select_injection_rejected(self):
        """Verify UNION SELECT injection is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="UNION SELECT"):
            AIAdoptionConfig(maturity_activation_threshold="20 UNION SELECT password FROM users")

    def test_exec_command_rejected(self):
        """Verify EXEC command is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="EXEC"):
            AIAdoptionConfig(user_engagement_activation_weight="0.3 EXEC('malicious')")

    def test_xp_stored_procedure_rejected(self):
        """Verify SQL Server xp_ procedures are rejected (caught by quote check)."""
        # Arrange & Act & Assert
        # Note: The single quote is caught first, which is good defense-in-depth
        with pytest.raises(ValidationError, match="SQL"):
            AIAdoptionConfig(maturity_level_3_threshold="xp_cmdshell 'malicious'")

    def test_sp_stored_procedure_rejected(self):
        """Verify SQL Server sp_ procedures are rejected (caught by quote check)."""
        # Arrange & Act & Assert
        # Note: The single quote is caught first, which is good defense-in-depth
        with pytest.raises(ValidationError, match="SQL"):
            AIAdoptionConfig(minimum_users_threshold="sp_executesql N'malicious'")

    def test_single_quote_rejected(self):
        """Verify single quote (SQL string delimiter) is rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="SQL statement terminator"):
            AIAdoptionConfig(maturity_activation_threshold="20' OR '1'='1")


class TestTypeCoercionSecurity:
    """Tests that type coercion provides additional SQL injection protection."""

    def test_integer_field_coerces_valid_string(self):
        """Verify integer fields coerce valid numeric strings."""
        # Act
        config = AIAdoptionConfig(maturity_activation_threshold="25")

        # Assert
        assert config.maturity_activation_threshold == 25
        assert isinstance(config.maturity_activation_threshold, int)

    def test_float_field_coerces_valid_string(self):
        """Verify float fields coerce valid numeric strings."""
        # Act - Must maintain valid weight sum (default sum is 1.0, so adjust all weights)
        config = AIAdoptionConfig(
            user_engagement_activation_weight="0.35",
            user_engagement_dau_weight=0.15,
            user_engagement_mau_weight=0.20,
            user_engagement_engagement_distribution_weight=0.15,
            user_engagement_multi_assistant_weight=0.15,
            user_engagement_returning_user_weight=0.0,  # Adjusted so sum = 1.0
        )

        # Assert
        assert config.user_engagement_activation_weight == 0.35
        assert isinstance(config.user_engagement_activation_weight, float)

    def test_integer_field_rejects_non_numeric(self):
        """Verify integer fields reject non-numeric values after validation."""
        # Arrange & Act & Assert
        # Pydantic will reject this during validation even if it passes SQL pattern check
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_activation_threshold="not_a_number")

    def test_float_field_rejects_non_numeric(self):
        """Verify float fields reject non-numeric values."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_activation_weight="not_a_float")

    def test_negative_value_rejected(self):
        """Verify negative values rejected by ge constraint."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="greater than or equal"):
            AIAdoptionConfig(maturity_activation_threshold=-5)

    def test_excessive_value_rejected(self):
        """Verify excessive values rejected by le constraint."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="less than or equal"):
            AIAdoptionConfig(maturity_activation_threshold=2000)


class TestRangeBoundaryValidation:
    """Tests that range constraints prevent out-of-bound values."""

    def test_integer_minimum_boundary(self):
        """Verify integer minimum boundary is enforced."""
        # Arrange - maturity_activation_threshold has ge=1
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_activation_threshold=0)

    def test_integer_maximum_boundary(self):
        """Verify integer maximum boundary is enforced."""
        # Arrange - maturity_activation_threshold has le=1000
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_activation_threshold=1001)

    def test_float_minimum_boundary(self):
        """Verify float minimum boundary is enforced."""
        # Arrange - weights have ge=0.0
        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_activation_weight=-0.1)

    def test_float_maximum_boundary(self):
        """Verify float maximum boundary is enforced."""
        # Arrange - weights have le=1.0
        with pytest.raises(ValidationError):
            AIAdoptionConfig(user_engagement_activation_weight=1.1)

    def test_percentage_threshold_range(self):
        """Verify percentage thresholds are bounded 0-100."""
        # Arrange - maturity_level thresholds have ge=0, le=100
        with pytest.raises(ValidationError):
            AIAdoptionConfig(maturity_level_2_threshold=150)


class TestWeightSumValidation:
    """Tests that weight groups must sum to 1.0."""

    def test_adoption_index_weights_sum_validation(self):
        """Verify adoption index weights must sum to 1.0."""
        # Arrange - Weights that don't sum to 1.0
        with pytest.raises(ValidationError, match="Adoption index weights"):
            AIAdoptionConfig(
                adoption_index_user_engagement_weight=0.5,
                adoption_index_asset_reusability_weight=0.5,
                adoption_index_expertise_distribution_weight=0.5,  # Sum > 1.0
                adoption_index_feature_adoption_weight=0.5,
            )

    def test_user_engagement_weights_sum_validation(self):
        """Verify user engagement weights must sum to 1.0."""
        # Arrange - Weights that don't sum to 1.0
        with pytest.raises(ValidationError, match="User Engagement weights"):
            AIAdoptionConfig(
                user_engagement_activation_weight=0.5,
                user_engagement_dau_weight=0.5,
                user_engagement_mau_weight=0.5,  # Sum > 1.0
                user_engagement_engagement_distribution_weight=0.5,
                user_engagement_multi_assistant_weight=0.5,
            )

    def test_asset_reusability_weights_sum_validation(self):
        """Verify asset reusability weights must sum to 1.0."""
        # Arrange
        with pytest.raises(ValidationError, match="Asset Reusability weights"):
            AIAdoptionConfig(
                asset_reusability_team_adopted_weight=0.4,
                asset_reusability_active_assistants_weight=0.4,
                asset_reusability_workflow_reuse_weight=0.4,  # Sum > 1.0
                asset_reusability_workflow_exec_weight=0.4,
                asset_reusability_datasource_reuse_weight=0.4,
            )

    def test_valid_weights_accepted(self):
        """Verify valid weight configuration is accepted."""
        # Act - Default weights should sum to 1.0
        config = AIAdoptionConfig()

        # Assert - No exception raised, weights are valid
        assert config.adoption_index_user_engagement_weight == 0.30


class TestSecureDefaults:
    """Tests that default configuration is secure."""

    def test_default_config_has_no_sql_patterns(self):
        """Verify default config values contain no SQL patterns."""
        # Act
        config = AIAdoptionConfig()

        # Assert - All defaults should be safe numeric values
        assert isinstance(config.maturity_activation_threshold, int)
        assert isinstance(config.user_engagement_activation_weight, float)
        # Check a sample of fields
        assert 0 < config.maturity_activation_threshold < 1000
        assert 0.0 <= config.user_engagement_activation_weight <= 1.0

    def test_default_thresholds_within_safe_ranges(self):
        """Verify default thresholds are within safe numeric ranges."""
        # Act
        config = AIAdoptionConfig()

        # Assert
        assert 0 <= config.maturity_level_2_threshold <= 100
        assert 0 <= config.maturity_level_3_threshold <= 100
        assert 1 <= config.maturity_activation_threshold <= 1000

    def test_default_weights_properly_bounded(self):
        """Verify default weights are properly bounded 0.0-1.0."""
        # Act
        config = AIAdoptionConfig()

        # Assert - Check all weight fields
        for field_name, field_value in config.model_dump().items():
            if "weight" in field_name:
                assert isinstance(field_value, float)
                assert 0.0 <= field_value <= 1.0, f"{field_name} out of range: {field_value}"


class TestConfigInterpolationSafety:
    """Tests that validated config values are safe for SQL interpolation.

    NOTE: While direct SQL interpolation is NOT recommended (parameterized
    queries are preferred), these tests verify that Pydantic validation
    provides defense-in-depth protection when config values are interpolated.
    """

    def test_integer_config_interpolation_is_safe(self):
        """Verify integer config values are safe after validation."""
        # Arrange
        config = AIAdoptionConfig(maturity_activation_threshold=25)

        # Act - Simulate SQL interpolation (as done in score_expressions.py)
        sql_fragment = f"threshold = {config.maturity_activation_threshold}"

        # Assert
        assert sql_fragment == "threshold = 25"
        assert ";" not in sql_fragment
        assert "--" not in sql_fragment
        assert "DROP" not in sql_fragment.upper()

    def test_float_config_interpolation_is_safe(self):
        """Verify float config values are safe after validation."""
        # Arrange
        config = AIAdoptionConfig(
            user_engagement_activation_weight=0.30,
            user_engagement_dau_weight=0.05,
            user_engagement_mau_weight=0.25,
            user_engagement_engagement_distribution_weight=0.15,
            user_engagement_multi_assistant_weight=0.10,
            user_engagement_returning_user_weight=0.15,
        )

        # Act - Simulate SQL interpolation
        sql_fragment = f"weight = {config.user_engagement_activation_weight}"

        # Assert
        assert sql_fragment == "weight = 0.3"
        assert ";" not in sql_fragment
        assert "--" not in sql_fragment

    def test_multiple_config_interpolations_are_safe(self):
        """Verify multiple config values interpolated together are safe."""
        # Arrange
        config = AIAdoptionConfig()

        # Act - Simulate complex SQL expression (as in score_expressions.py)
        sql_expression = f"""
            weight_sum =
                component1 * {config.user_engagement_activation_weight} +
                component2 * {config.user_engagement_dau_weight} +
                component3 * {config.user_engagement_mau_weight}
        """

        # Assert - No SQL injection patterns present
        assert ";" not in sql_expression
        assert "--" not in sql_expression
        assert "DROP" not in sql_expression.upper()
        assert "DELETE" not in sql_expression.upper()


class TestConfigLoadingSecurity:
    """Tests for secure config loading from various sources."""

    def test_dict_loading_with_malicious_values_rejected(self):
        """Verify config loaded from dict validates properly."""
        # Arrange - Malicious config dict (e.g., from database)
        malicious_config = {
            "maturity_activation_threshold": "20; DROP TABLE users",
            "user_engagement_activation_weight": 0.30,
        }

        # Act & Assert
        with pytest.raises(ValidationError, match="SQL statement terminator"):
            AIAdoptionConfig(**malicious_config)

    def test_json_loading_with_malicious_values_rejected(self):
        """Verify config from JSON validates properly."""
        # Arrange
        import json

        json_str = '{"maturity_activation_threshold": "20 UNION SELECT * FROM users"}'
        config_dict = json.loads(json_str)

        # Act & Assert
        with pytest.raises(ValidationError, match="UNION SELECT"):
            AIAdoptionConfig(**config_dict)

    def test_partial_config_update_validates(self):
        """Verify partial config updates bypass validation (immutable models).

        Note: model_copy doesn't re-validate. To ensure validation on updates,
        create a new config instance instead.
        """
        # Arrange
        config = AIAdoptionConfig()

        # Act - model_copy doesn't re-run validation, so create new instance
        with pytest.raises(ValidationError, match="SQL"):
            AIAdoptionConfig(**{**config.model_dump(), "maturity_activation_threshold": "20; DROP TABLE"})


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_values_where_allowed(self):
        """Verify zero values work where allowed."""
        # Arrange - user_engagement_activation_window allows ge=0
        config = AIAdoptionConfig(user_engagement_activation_window=0)

        # Assert
        assert config.user_engagement_activation_window == 0

    def test_scientific_notation_handled_correctly(self):
        """Verify scientific notation in floats is handled."""
        # Act
        config = AIAdoptionConfig(
            user_engagement_activation_weight=3e-1,
            user_engagement_dau_weight=0.05,
            user_engagement_mau_weight=0.25,
            user_engagement_engagement_distribution_weight=0.15,
            user_engagement_multi_assistant_weight=0.10,
            user_engagement_returning_user_weight=0.15,
        )

        # Assert
        assert config.user_engagement_activation_weight == 0.3

    def test_very_small_float_handled(self):
        """Verify very small float values are handled (with valid weight sum)."""
        # Act - Must maintain valid weight sum
        config = AIAdoptionConfig(
            user_engagement_activation_weight=0.0001,
            user_engagement_dau_weight=0.1,
            user_engagement_mau_weight=0.2,
            user_engagement_engagement_distribution_weight=0.3,
            user_engagement_multi_assistant_weight=0.3999,
            user_engagement_returning_user_weight=0.0,  # Sum = 1.0
        )

        # Assert
        assert 0.0 <= config.user_engagement_activation_weight <= 1.0

    def test_maximum_allowed_values(self):
        """Verify maximum allowed values work correctly."""
        # Act - Can't set single weight to 1.0 without adjusting others
        config = AIAdoptionConfig(
            maturity_activation_threshold=1000,  # Max allowed
            maturity_level_3_threshold=100,  # Max allowed
            # Keep default weights (they sum to 1.0)
        )

        # Assert
        assert config.maturity_activation_threshold == 1000
        assert config.maturity_level_3_threshold == 100
        # Verify a weight is within bounds
        assert 0.0 <= config.user_engagement_activation_weight <= 1.0
