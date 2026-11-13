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

"""Schema validation tests for project budget router models."""

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.project_budget import (
    BulkMemberOverrideItem,
    BulkMemberOverrideRequest,
    BulkMemberOverrideResponse,
    BulkMemberOverrideResult,
    CategoryBudgetSpec,
    CategoryBudgetSpecUpdate,
)


class TestCategoryBudgetSpecValidation:
    """Test CategoryBudgetSpec.pct field validation."""

    def test_accepts_zero_percent(self):
        """CategoryBudgetSpec should accept pct=0 for zero-allocation categories."""
        spec = CategoryBudgetSpec(pct=0.0, soft_budget=None)
        assert spec.pct == 0.0

    def test_accepts_positive_percent(self):
        """CategoryBudgetSpec should accept positive pct values."""
        spec = CategoryBudgetSpec(pct=50.0, soft_budget=None)
        assert spec.pct == 50.0

    def test_accepts_negative_percent(self):
        """CategoryBudgetSpec accepts negative pct at model level; service raises 400."""
        spec = CategoryBudgetSpec(pct=-1.0, soft_budget=None)
        assert spec.pct == -1.0

    def test_accepts_percent_over_100(self):
        """CategoryBudgetSpec accepts pct>100 at model level; service raises 400."""
        spec = CategoryBudgetSpec(pct=101.0, soft_budget=None)
        assert spec.pct == 101.0


class TestCategoryBudgetSpecUpdateValidation:
    """Test CategoryBudgetSpecUpdate.pct already accepts zero (reference behavior)."""

    def test_update_accepts_zero_percent(self):
        """CategoryBudgetSpecUpdate already accepts pct=0 (no change needed)."""
        spec = CategoryBudgetSpecUpdate(pct=0.0, soft_budget=None)
        assert spec.pct == 0.0


def _bulk_item(**overrides):
    data = {"budget_id": "b-1", "user_id": "u-1", "allocated_max_budget": 10.0, "allocated_soft_budget": 5.0}
    data.update(overrides)
    return data


class TestBulkMemberOverrideRequestValidation:
    def test_bulk_request_rejects_empty_items(self):
        with pytest.raises(ValidationError):
            BulkMemberOverrideRequest(items=[])

    def test_bulk_request_rejects_1001_items(self):
        with pytest.raises(ValidationError):
            BulkMemberOverrideRequest(items=[_bulk_item(user_id=f"u-{i}") for i in range(1001)])

    def test_bulk_request_rejects_negative_amounts(self):
        with pytest.raises(ValidationError):
            BulkMemberOverrideRequest(items=[_bulk_item(allocated_max_budget=-1.0)])
        with pytest.raises(ValidationError):
            BulkMemberOverrideRequest(items=[_bulk_item(allocated_soft_budget=-0.5)])

    def test_bulk_request_rejects_override_reason_over_500_chars(self):
        with pytest.raises(ValidationError):
            BulkMemberOverrideRequest(items=[_bulk_item(override_reason="x" * 501)])

    def test_bulk_request_accepts_1000_items_with_null_reason(self):
        request = BulkMemberOverrideRequest(items=[_bulk_item(user_id=f"u-{i}") for i in range(1000)])

        assert len(request.items) == 1000
        assert all(isinstance(item, BulkMemberOverrideItem) for item in request.items)
        assert request.items[0].override_reason is None


class TestBulkMemberOverrideResponseModels:
    def test_result_defaults_code_and_message_to_none(self):
        result = BulkMemberOverrideResult(budget_id="b-1", user_id="u-1", status="ok")

        assert result.code is None
        assert result.message is None

    def test_ok_result_serializes_without_code_and_message(self):
        result = BulkMemberOverrideResult(budget_id="b-1", user_id="u-1", status="ok")

        assert result.model_dump(mode="json") == {"budget_id": "b-1", "user_id": "u-1", "status": "ok"}

    def test_failed_result_serializes_code_and_message(self):
        result = BulkMemberOverrideResult(budget_id="b-1", user_id="u-1", status="failed", code=404, message="gone")

        assert result.model_dump(mode="json") == {
            "budget_id": "b-1",
            "user_id": "u-1",
            "status": "failed",
            "code": 404,
            "message": "gone",
        }

    def test_result_rejects_unknown_status(self):
        with pytest.raises(ValidationError):
            BulkMemberOverrideResult(budget_id="b-1", user_id="u-1", status="maybe")

    def test_response_holds_results_and_budgets(self):
        response = BulkMemberOverrideResponse(
            results=[BulkMemberOverrideResult(budget_id="b-1", user_id="u-1", status="failed", code=404)],
            budgets=[],
        )

        assert response.results[0].code == 404
        assert response.budgets == []
