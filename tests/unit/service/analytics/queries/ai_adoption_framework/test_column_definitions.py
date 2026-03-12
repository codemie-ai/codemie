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

"""Unit tests for column_definitions module.

Tests column metadata structure, helper functions, and data integrity.
"""

from __future__ import annotations


from codemie.service.analytics.queries.ai_adoption_framework.column_definitions import (
    ASSET_REUSABILITY_COLUMNS,
    BASELINE_COLUMNS,
    COMPOSITE_COLUMNS,
    DIMENSION_SCORE_COLUMNS,
    EXPERTISE_DISTRIBUTION_COLUMNS,
    FEATURE_ADOPTION_COLUMNS,
    USER_ENGAGEMENT_COLUMNS,
    get_asset_reusability_detail_columns,
    get_dimensions_columns,
    get_expertise_distribution_detail_columns,
    get_feature_adoption_detail_columns,
    get_maturity_metrics,
    get_user_engagement_detail_columns,
)


class TestColumnConstants:
    """Test that column constant definitions are valid."""

    def test_baseline_columns_structure(self):
        """Test baseline columns have required fields."""
        assert len(BASELINE_COLUMNS) == 1
        assert BASELINE_COLUMNS[0]["id"] == "project"
        assert BASELINE_COLUMNS[0]["type"] == "string"
        assert "label" in BASELINE_COLUMNS[0]
        assert "description" in BASELINE_COLUMNS[0]

    def test_d1_columns_count(self):
        """Test D1 has 6 columns."""
        assert len(USER_ENGAGEMENT_COLUMNS) == 6

    def test_d2_columns_count(self):
        """Test D2 has 8 columns."""
        assert len(ASSET_REUSABILITY_COLUMNS) == 8

    def test_d3_columns_count(self):
        """Test D3 has 4 columns."""
        assert len(EXPERTISE_DISTRIBUTION_COLUMNS) == 4

    def test_d4_columns_count(self):
        """Test D4 has 7 columns."""
        assert len(FEATURE_ADOPTION_COLUMNS) == 7

    def test_composite_columns_count(self):
        """Test composite columns has 2 items."""
        assert len(COMPOSITE_COLUMNS) == 2

    def test_dimension_score_columns_count(self):
        """Test dimension score columns has 4 items."""
        assert len(DIMENSION_SCORE_COLUMNS) == 4

    def test_all_columns_have_required_fields(self):
        """Test all column definitions have required fields."""
        all_columns = (
            BASELINE_COLUMNS
            + USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
            + COMPOSITE_COLUMNS
            + DIMENSION_SCORE_COLUMNS
        )

        for column in all_columns:
            assert "id" in column, f"Column missing 'id': {column}"
            assert "label" in column, f"Column missing 'label': {column}"
            assert "type" in column, f"Column missing 'type': {column}"
            assert "description" in column, f"Column missing 'description': {column}"
            assert isinstance(column["id"], str), f"Column id must be string: {column}"
            assert isinstance(column["label"], str), f"Column label must be string: {column}"
            assert isinstance(column["type"], str), f"Column type must be string: {column}"
            assert isinstance(column["description"], str), f"Column description must be string: {column}"

    def test_column_types_are_valid(self):
        """Test all column types are from valid set."""
        valid_types = {"string", "number", "integer"}
        all_columns = (
            BASELINE_COLUMNS
            + USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
            + COMPOSITE_COLUMNS
            + DIMENSION_SCORE_COLUMNS
        )

        for column in all_columns:
            assert (
                column["type"] in valid_types
            ), f"Invalid type '{column['type']}' in column '{column['id']}'. Valid types: {valid_types}"

    def test_column_formats_are_valid(self):
        """Test all column formats (if present) are from valid set."""
        valid_formats = {"score", "percentage", "string"}
        all_columns = (
            BASELINE_COLUMNS
            + USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
            + COMPOSITE_COLUMNS
            + DIMENSION_SCORE_COLUMNS
        )

        for column in all_columns:
            if "format" in column:
                assert (
                    column["format"] in valid_formats
                ), f"Invalid format '{column['format']}' in column '{column['id']}'. Valid formats: {valid_formats}"

    def test_dimension_score_columns_all_have_score_format(self):
        """Test all dimension score columns have score format."""
        for column in DIMENSION_SCORE_COLUMNS:
            assert column["format"] == "score", f"Dimension score column '{column['id']}' must have 'score' format"
            assert column["type"] == "number", f"Dimension score column '{column['id']}' must have 'number' type"


class TestHelperFunctions:
    """Test helper functions return correct column sets."""

    def test_get_maturity_metrics_count(self):
        """Test get_maturity_metrics returns 2 metrics."""
        metrics = get_maturity_metrics()
        assert len(metrics) == 2

    def test_get_maturity_metrics_structure(self):
        """Test get_maturity_metrics returns correct metric definitions."""
        metrics = get_maturity_metrics()
        expected_ids = [
            "maturity_level",
            "adoption_index",
        ]
        actual_ids = [m["id"] for m in metrics]
        assert actual_ids == expected_ids

        # Test all metrics have required fields
        for metric in metrics:
            assert "id" in metric
            assert "label" in metric
            assert "format" in metric
            assert "description" in metric

    def test_get_dimensions_columns_count(self):
        """Test get_dimensions_columns returns 32 columns."""
        columns = get_dimensions_columns()
        # 1 (baseline) + 6 (D1) + 8 (D2) + 4 (D3) + 7 (D4) + 2 (composite) + 4 (dimension scores) = 32
        assert len(columns) == 32

    def test_get_dimensions_columns_structure(self):
        """Test get_dimensions_columns returns columns in correct order."""
        columns = get_dimensions_columns()

        # First column should be project
        assert columns[0]["id"] == "project"

        # Extract all column IDs
        column_ids = [c["id"] for c in columns]

        # Note: Intentional duplicates exist (total_assistants, total_workflows, dimension scores)
        # This is by design to include both detail metrics and summary scores
        expected_duplicates = {
            "total_assistants",
            "total_workflows",
            "user_engagement_score",
            "asset_reusability_score",
            "expertise_distribution_score",
            "feature_adoption_score",
        }
        actual_duplicates = {id for id in column_ids if column_ids.count(id) > 1}
        assert actual_duplicates == expected_duplicates, f"Unexpected duplicate IDs: {actual_duplicates}"

    def test_get_user_engagement_detail_columns_count(self):
        """Test get_user_engagement_detail_columns returns 9 columns."""
        columns = get_user_engagement_detail_columns()
        # 1 (baseline) + 5 (D1 base) + 3 (D1 detail only) = 9
        assert len(columns) == 9

    def test_get_user_engagement_detail_columns_structure(self):
        """Test get_user_engagement_detail_columns returns correct columns."""
        columns = get_user_engagement_detail_columns()

        # First column should be project
        assert columns[0]["id"] == "project"

        # Second column should be user_engagement_score
        assert columns[1]["id"] == "user_engagement_score"

        # All column IDs should be unique
        column_ids = [c["id"] for c in columns]
        assert len(column_ids) == len(set(column_ids))

    def test_get_asset_reusability_detail_columns_count(self):
        """Test get_asset_reusability_detail_columns returns 11 columns."""
        columns = get_asset_reusability_detail_columns()
        # 1 (baseline) + 8 (D2 base) + 2 (D2 detail only) = 11
        assert len(columns) == 11

    def test_get_asset_reusability_detail_columns_structure(self):
        """Test get_asset_reusability_detail_columns returns correct columns."""
        columns = get_asset_reusability_detail_columns()

        # First column should be project
        assert columns[0]["id"] == "project"

        # Second column should be asset_reusability_score
        assert columns[1]["id"] == "asset_reusability_score"

        # All column IDs should be unique
        column_ids = [c["id"] for c in columns]
        assert len(column_ids) == len(set(column_ids))

    def test_get_expertise_distribution_detail_columns_count(self):
        """Test get_expertise_distribution_detail_columns returns 5 columns."""
        columns = get_expertise_distribution_detail_columns()
        # 1 (baseline) + 4 (D3) = 5
        assert len(columns) == 5

    def test_get_expertise_distribution_detail_columns_structure(self):
        """Test get_expertise_distribution_detail_columns returns correct columns."""
        columns = get_expertise_distribution_detail_columns()

        # First column should be project
        assert columns[0]["id"] == "project"

        # Second column should be expertise_distribution_score
        assert columns[1]["id"] == "expertise_distribution_score"

        # All column IDs should be unique
        column_ids = [c["id"] for c in columns]
        assert len(column_ids) == len(set(column_ids))

    def test_get_feature_adoption_detail_columns_count(self):
        """Test get_feature_adoption_detail_columns returns 8 columns."""
        columns = get_feature_adoption_detail_columns()
        # 1 (baseline) + 7 (D4) = 8
        assert len(columns) == 8

    def test_get_feature_adoption_detail_columns_structure(self):
        """Test get_feature_adoption_detail_columns returns correct columns."""
        columns = get_feature_adoption_detail_columns()

        # First column should be project
        assert columns[0]["id"] == "project"

        # Second column should be feature_adoption_score
        assert columns[1]["id"] == "feature_adoption_score"

        # All column IDs should be unique
        column_ids = [c["id"] for c in columns]
        assert len(column_ids) == len(set(column_ids))


class TestColumnIDUniqueness:
    """Test that column IDs are unique within each function."""

    def test_d1_columns_unique_ids(self):
        """Test D1 columns have unique IDs."""
        column_ids = [c["id"] for c in USER_ENGAGEMENT_COLUMNS]
        assert len(column_ids) == len(set(column_ids))

    def test_d2_columns_unique_ids(self):
        """Test D2 columns have unique IDs."""
        column_ids = [c["id"] for c in ASSET_REUSABILITY_COLUMNS]
        assert len(column_ids) == len(set(column_ids))

    def test_d3_columns_unique_ids(self):
        """Test D3 columns have unique IDs."""
        column_ids = [c["id"] for c in EXPERTISE_DISTRIBUTION_COLUMNS]
        assert len(column_ids) == len(set(column_ids))

    def test_d4_columns_unique_ids(self):
        """Test D4 columns have unique IDs."""
        column_ids = [c["id"] for c in FEATURE_ADOPTION_COLUMNS]
        assert len(column_ids) == len(set(column_ids))

    def test_dimensions_columns_has_expected_duplicates(self):
        """Test get_dimensions_columns has expected duplicate column IDs (by design)."""
        columns = get_dimensions_columns()
        column_ids = [c["id"] for c in columns]

        # These duplicates are intentional to provide both detail and summary views
        expected_duplicates = {
            "total_assistants",
            "total_workflows",
            "user_engagement_score",
            "asset_reusability_score",
            "expertise_distribution_score",
            "feature_adoption_score",
        }
        actual_duplicates = {id for id in column_ids if column_ids.count(id) > 1}

        assert (
            actual_duplicates == expected_duplicates
        ), f"Expected duplicates: {expected_duplicates}, Actual duplicates: {actual_duplicates}"


class TestScoreColumns:
    """Test that all score columns are properly defined."""

    def test_all_dimension_scores_present(self):
        """Test all 4 dimension scores are defined."""
        score_ids = [c["id"] for c in DIMENSION_SCORE_COLUMNS]
        assert "user_engagement_score" in score_ids
        assert "asset_reusability_score" in score_ids
        assert "expertise_distribution_score" in score_ids
        assert "feature_adoption_score" in score_ids

    def test_user_engagement_score_in_d1_columns(self):
        """Test user_engagement_score is first metric in D1 columns."""
        assert USER_ENGAGEMENT_COLUMNS[0]["id"] == "user_engagement_score"

    def test_asset_reusability_score_in_d2_columns(self):
        """Test asset_reusability_score is first metric in D2 columns."""
        assert ASSET_REUSABILITY_COLUMNS[0]["id"] == "asset_reusability_score"

    def test_expertise_distribution_score_in_d3_columns(self):
        """Test expertise_distribution_score is first metric in D3 columns."""
        assert EXPERTISE_DISTRIBUTION_COLUMNS[0]["id"] == "expertise_distribution_score"

    def test_feature_adoption_score_in_d4_columns(self):
        """Test feature_adoption_score is first metric in D4 columns."""
        assert FEATURE_ADOPTION_COLUMNS[0]["id"] == "feature_adoption_score"


class TestCompositeColumns:
    """Test composite column definitions."""

    def test_adoption_index_present(self):
        """Test adoption_index is in composite columns."""
        composite_ids = [c["id"] for c in COMPOSITE_COLUMNS]
        assert "adoption_index" in composite_ids

    def test_maturity_level_present(self):
        """Test maturity_level is in composite columns."""
        composite_ids = [c["id"] for c in COMPOSITE_COLUMNS]
        assert "maturity_level" in composite_ids

    def test_adoption_index_is_number(self):
        """Test adoption_index has correct type and format."""
        adoption_index = next(c for c in COMPOSITE_COLUMNS if c["id"] == "adoption_index")
        assert adoption_index["type"] == "number"
        assert adoption_index["format"] == "score"

    def test_maturity_level_is_string(self):
        """Test maturity_level has correct type."""
        maturity_level = next(c for c in COMPOSITE_COLUMNS if c["id"] == "maturity_level")
        assert maturity_level["type"] == "string"


class TestDescriptionQuality:
    """Test that descriptions are meaningful and not empty."""

    def test_no_empty_descriptions(self):
        """Test all columns have non-empty descriptions."""
        all_columns = (
            BASELINE_COLUMNS
            + USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
            + COMPOSITE_COLUMNS
            + DIMENSION_SCORE_COLUMNS
        )

        for column in all_columns:
            assert len(column["description"]) > 0, f"Empty description for column '{column['id']}'"
            assert len(column["description"]) > 10, f"Description too short for column '{column['id']}'"

    def test_no_empty_labels(self):
        """Test all columns have non-empty labels."""
        all_columns = (
            BASELINE_COLUMNS
            + USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
            + COMPOSITE_COLUMNS
            + DIMENSION_SCORE_COLUMNS
        )

        for column in all_columns:
            assert len(column["label"]) > 0, f"Empty label for column '{column['id']}'"


class TestColumnFormatConsistency:
    """Test that columns with same type of data have consistent formats."""

    def test_all_scores_have_score_format(self):
        """Test all columns ending with '_score' have 'score' format."""
        all_columns = (
            USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
            + COMPOSITE_COLUMNS
            + DIMENSION_SCORE_COLUMNS
        )

        for column in all_columns:
            if column["id"].endswith("_score") or column["id"] == "adoption_index":
                assert "format" in column, f"Score column '{column['id']}' missing 'format' field"
                assert column["format"] == "score", f"Score column '{column['id']}' should have 'score' format"

    def test_all_rates_have_percentage_format(self):
        """Test all columns ending with '_rate' or '_ratio' have 'percentage' format."""
        all_columns = (
            USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
        )

        for column in all_columns:
            if column["id"].endswith("_rate") or column["id"].endswith("_ratio"):
                assert "format" in column, f"Rate/ratio column '{column['id']}' missing 'format' field"
                assert (
                    column["format"] == "percentage"
                ), f"Rate/ratio column '{column['id']}' should have 'percentage' format"

    def test_total_columns_are_integers(self):
        """Test all columns starting with 'total_' have 'integer' type."""
        all_columns = (
            USER_ENGAGEMENT_COLUMNS
            + ASSET_REUSABILITY_COLUMNS
            + EXPERTISE_DISTRIBUTION_COLUMNS
            + FEATURE_ADOPTION_COLUMNS
        )

        for column in all_columns:
            if column["id"].startswith("total_"):
                assert column["type"] == "integer", f"Total column '{column['id']}' should have 'integer' type"
