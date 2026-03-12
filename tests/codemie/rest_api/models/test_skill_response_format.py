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

"""Tests to verify skill response model JSON field format."""

import json
from datetime import UTC, datetime


from codemie.core.models import CreatedByUser
from codemie.rest_api.models.skill import (
    SkillCategory,
    SkillDetailResponse,
    SkillListResponse,
    SkillVisibility,
)


class TestSkillResponseFieldNaming:
    """Test that response models use snake_case for all fields."""

    def test_skill_list_response_uses_snake_case(self):
        """Verify SkillListResponse serializes fields in snake_case."""
        # Arrange
        created_by = CreatedByUser(id="user-123", username="testuser", name="Test User")
        skill_response = SkillListResponse(
            id="skill-123",
            name="test-skill",
            description="Test description",
            project="test-project",
            visibility=SkillVisibility.PUBLIC,
            created_by=created_by,
            categories=[SkillCategory.DEVELOPMENT],
            created_date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_date=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
            is_attached=True,
            assistants_count=5,
            user_abilities=["read", "write"],
            unique_likes_count=10,
            unique_dislikes_count=2,
        )

        # Act
        json_str = skill_response.model_dump_json()
        json_data = json.loads(json_str)

        # Assert - check snake_case fields exist
        assert "is_attached" in json_data
        assert "assistants_count" in json_data
        assert "unique_likes_count" in json_data
        assert "unique_dislikes_count" in json_data

        # Assert - verify camelCase fields do NOT exist
        assert "isAttached" not in json_data
        assert "assistantsCount" not in json_data
        assert "uniqueLikesCount" not in json_data
        assert "uniqueDislikesCount" not in json_data

        # Assert - verify values
        assert json_data["is_attached"] is True
        assert json_data["assistants_count"] == 5
        assert json_data["unique_likes_count"] == 10
        assert json_data["unique_dislikes_count"] == 2

    def test_skill_detail_response_uses_snake_case(self):
        """Verify SkillDetailResponse serializes fields in snake_case."""
        # Arrange
        created_by = CreatedByUser(id="user-123", username="testuser", name="Test User")
        skill_response = SkillDetailResponse(
            id="skill-123",
            name="test-skill",
            description="Test description",
            content="# Test Content",
            project="test-project",
            visibility=SkillVisibility.PRIVATE,
            created_by=created_by,
            categories=[SkillCategory.TESTING, SkillCategory.DOCUMENTATION],
            created_date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_date=None,
            assistants_count=3,
            user_abilities=["read", "write", "delete"],
            unique_likes_count=15,
            unique_dislikes_count=1,
        )

        # Act
        json_str = skill_response.model_dump_json()
        json_data = json.loads(json_str)

        # Assert - check snake_case fields exist
        assert "assistants_count" in json_data
        assert "unique_likes_count" in json_data
        assert "unique_dislikes_count" in json_data

        # Assert - verify camelCase fields do NOT exist
        assert "assistantsCount" not in json_data
        assert "uniqueLikesCount" not in json_data
        assert "uniqueDislikesCount" not in json_data

        # Assert - verify values
        assert json_data["assistants_count"] == 3
        assert json_data["unique_likes_count"] == 15
        assert json_data["unique_dislikes_count"] == 1

    def test_created_updated_dates_use_camel_case_with_by_alias(self):
        """Verify that created_date and updated_date use camelCase when by_alias=True (for backward compatibility)."""
        # Arrange
        created_by = CreatedByUser(id="user-123", username="testuser", name="Test User")
        skill_response = SkillListResponse(
            id="skill-123",
            name="test-skill",
            description="Test description",
            project="test-project",
            visibility=SkillVisibility.PUBLIC,
            created_by=created_by,
            categories=[],
            created_date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_date=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
        )

        # Act - Serialization with by_alias=True
        json_str = skill_response.model_dump_json(by_alias=True)
        json_data = json.loads(json_str)

        # Assert - these fields use camelCase for backward compatibility
        assert "createdDate" in json_data
        assert "updatedDate" in json_data

    def test_snake_case_fields_remain_snake_case_even_with_by_alias(self):
        """Verify that fields without serialization_alias stay snake_case even with by_alias=True."""
        # Arrange
        created_by = CreatedByUser(id="user-123", username="testuser", name="Test User")
        skill_response = SkillListResponse(
            id="skill-123",
            name="test-skill",
            description="Test description",
            project="test-project",
            visibility=SkillVisibility.PUBLIC,
            created_by=created_by,
            categories=[],
            created_date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            is_attached=True,
            assistants_count=5,
            unique_likes_count=10,
            unique_dislikes_count=2,
        )

        # Act - Use by_alias=True
        json_str = skill_response.model_dump_json(by_alias=True)
        json_data = json.loads(json_str)

        # Assert - these fields should remain snake_case (no serialization_alias)
        assert "is_attached" in json_data
        assert "assistants_count" in json_data
        assert "unique_likes_count" in json_data
        assert "unique_dislikes_count" in json_data

        # Assert - verify camelCase versions do NOT exist
        assert "isAttached" not in json_data
        assert "assistantsCount" not in json_data
        assert "uniqueLikesCount" not in json_data
        assert "uniqueDislikesCount" not in json_data
