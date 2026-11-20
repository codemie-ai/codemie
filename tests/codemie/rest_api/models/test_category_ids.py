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

"""
Tests for category selection types: CategoryIds and RequiredCategoryIds
"""

import pytest
from pydantic import BaseModel, Field, ValidationError

from codemie.rest_api.models.category import CategoryIds, RequiredCategoryIds


class CategoryIdsModel(BaseModel):
    """Model for testing CategoryIds"""

    ids: CategoryIds


class RequiredCategoryIdsModel(BaseModel):
    """Model for testing RequiredCategoryIds"""

    ids: RequiredCategoryIds = Field(default=[], validate_default=True)


class TestCategoryIds:
    """Tests for CategoryIds - optional 0-3 categories"""

    def test_accepts_empty_list(self):
        """CategoryIds accepts empty list"""
        model = CategoryIdsModel(ids=[])
        assert model.ids == []

    def test_accepts_single_id(self):
        """CategoryIds accepts single ID"""
        model = CategoryIdsModel(ids=["category1"])
        assert model.ids == ["category1"]

    def test_accepts_two_ids(self):
        """CategoryIds accepts two IDs"""
        model = CategoryIdsModel(ids=["cat1", "cat2"])
        assert model.ids == ["cat1", "cat2"]

    def test_accepts_three_ids(self):
        """CategoryIds accepts three IDs"""
        model = CategoryIdsModel(ids=["cat1", "cat2", "cat3"])
        assert model.ids == ["cat1", "cat2", "cat3"]

    def test_rejects_four_ids(self):
        """CategoryIds rejects 4 IDs with proper error message"""
        with pytest.raises(ValidationError) as exc_info:
            CategoryIdsModel(ids=["cat1", "cat2", "cat3", "cat4"])
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "Maximum 3 categories allowed" in str(errors[0])

    def test_rejects_duplicate_ids(self):
        """CategoryIds rejects duplicate IDs with proper error message"""
        with pytest.raises(ValidationError) as exc_info:
            CategoryIdsModel(ids=["a", "a"])
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "category IDs must be unique" in str(errors[0])

    def test_rejects_non_string_id(self):
        """CategoryIds rejects non-string IDs"""
        with pytest.raises(ValidationError):
            CategoryIdsModel(ids=[5])  # type: ignore

    def test_rejects_id_too_long(self):
        """CategoryIds rejects ID longer than 256 characters"""
        long_id = "a" * 257
        with pytest.raises(ValidationError):
            CategoryIdsModel(ids=[long_id])

    def test_accepts_id_exactly_256_chars(self):
        """CategoryIds accepts ID of exactly 256 characters"""
        id_256 = "a" * 256
        model = CategoryIdsModel(ids=[id_256])
        assert model.ids == [id_256]


class TestRequiredCategoryIds:
    """Tests for RequiredCategoryIds - required 1-3 categories"""

    def test_rejects_empty_list(self):
        """RequiredCategoryIds rejects empty list"""
        with pytest.raises(ValidationError) as exc_info:
            RequiredCategoryIdsModel(ids=[])
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "At least one category is required" in str(errors[0])

    def test_rejects_no_field(self):
        """RequiredCategoryIds rejects model constructed with no field"""
        with pytest.raises(ValidationError) as exc_info:
            RequiredCategoryIdsModel()
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "At least one category is required" in str(errors[0])

    def test_accepts_single_id(self):
        """RequiredCategoryIds accepts single ID"""
        model = RequiredCategoryIdsModel(ids=["category1"])
        assert model.ids == ["category1"]

    def test_accepts_two_ids(self):
        """RequiredCategoryIds accepts two IDs"""
        model = RequiredCategoryIdsModel(ids=["cat1", "cat2"])
        assert model.ids == ["cat1", "cat2"]

    def test_accepts_three_ids(self):
        """RequiredCategoryIds accepts three IDs"""
        model = RequiredCategoryIdsModel(ids=["cat1", "cat2", "cat3"])
        assert model.ids == ["cat1", "cat2", "cat3"]

    def test_rejects_four_ids(self):
        """RequiredCategoryIds rejects 4 IDs with proper error message"""
        with pytest.raises(ValidationError) as exc_info:
            RequiredCategoryIdsModel(ids=["cat1", "cat2", "cat3", "cat4"])
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "Maximum 3 categories allowed" in str(errors[0])

    def test_rejects_duplicate_ids(self):
        """RequiredCategoryIds rejects duplicate IDs with proper error message"""
        with pytest.raises(ValidationError) as exc_info:
            RequiredCategoryIdsModel(ids=["a", "a"])
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "category IDs must be unique" in str(errors[0])

    def test_rejects_non_string_id(self):
        """RequiredCategoryIds rejects non-string IDs"""
        with pytest.raises(ValidationError):
            RequiredCategoryIdsModel(ids=[5])  # type: ignore

    def test_rejects_id_too_long(self):
        """RequiredCategoryIds rejects ID longer than 256 characters"""
        long_id = "a" * 257
        with pytest.raises(ValidationError):
            RequiredCategoryIdsModel(ids=[long_id])

    def test_accepts_id_exactly_256_chars(self):
        """RequiredCategoryIds accepts ID of exactly 256 characters"""
        id_256 = "a" * 256
        model = RequiredCategoryIdsModel(ids=[id_256])
        assert model.ids == [id_256]
