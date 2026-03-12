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

import unittest
from unittest.mock import patch, MagicMock, Mock

import httpx
import pytest

from codemie.configs.config import config
from codemie.configs.llm_config import (
    LLMModel,
    LLMProvider,
    LLMFeatures,
    ModelCategory,
    LiteLLMModels,
)
from codemie.enterprise.loader import (
    BudgetTable,
    CustomerInfo,
    KeySpendingInfo,
    LiteLLMService,
    HAS_LITELLM,
)
from codemie.triggers.bindings.cache_manager import CacheManager

# Default timeout from LiteLLMConfig
DEFAULT_REQUEST_TIMEOUT = 5.0

# Skip all tests in this file - testing deprecated functionality or enterprise not installed
pytestmark = pytest.mark.skip(
    reason="Testing deprecated LiteLLM service - migrated to enterprise package. New tests needed for enterprise architecture."
    if HAS_LITELLM
    else "Enterprise package not installed - codemie_enterprise module not available"
)


class TestLiteLLMService(unittest.TestCase):
    """Test suite for LiteLLMService class"""

    def setUp(self):
        self.service = LiteLLMService()

    def test_init(self):
        """Test initialization of LiteLLMService"""
        self.assertEqual(self.service.base_url, config.LITE_LLM_URL)
        self.assertIsNotNone(self.service.cache)
        self.assertIsNotNone(self.service.models_cache)


@pytest.mark.parametrize(
    "budget_id, expected_exists",
    [
        ("existing-budget", True),
        ("non-existing-budget", False),
    ],
)
@patch("httpx.Client")
def test_get_budget_info(mock_client, budget_id, expected_exists):
    """Test get_budget_info method for existing and non-existing budgets"""
    service = LiteLLMService()

    mock_response = MagicMock()
    mock_client.return_value.__enter__.return_value.post.return_value = mock_response

    # Set up response data based on whether budget should exist
    if expected_exists:
        mock_response.json.return_value = [
            {"budget_id": budget_id, "soft_budget": 100.0, "max_budget": 200.0, "budget_duration": "30d"}
        ]
    else:
        mock_response.json.return_value = []

    # Act
    result = service.get_budget_info([budget_id])

    # Assert
    mock_client.assert_called_once_with(timeout=DEFAULT_REQUEST_TIMEOUT)
    expected_headers = service._get_headers()
    mock_client.return_value.__enter__.return_value.post.assert_called_once_with(
        f"{config.LITE_LLM_URL}/budget/info", headers=expected_headers, json={"budgets": [budget_id]}
    )

    if expected_exists:
        assert len(result) == 1
        assert isinstance(result[0], BudgetTable)
        assert result[0].budget_id == budget_id
    else:
        assert len(result) == 0


@patch("httpx.Client")
def test_get_budget_info_http_error(mock_client):
    """Test get_budget_info method when HTTP error occurs"""
    # Arrange
    service = LiteLLMService()

    mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPError("Test HTTP Error")

    # Act
    result = service.get_budget_info(["test-budget-id"])

    # Assert
    assert len(result) == 0


@patch("httpx.Client")
def test_create_budget_success(mock_client):
    """Test create_budget method success case"""
    # Arrange
    service = LiteLLMService()

    budget_id = "test-budget-id"
    max_budget = 200.0
    soft_budget = 100.0
    budget_duration = "30d"

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "budget_id": budget_id,
        "soft_budget": soft_budget,
        "max_budget": max_budget,
        "budget_duration": budget_duration,
    }
    mock_client.return_value.__enter__.return_value.post.return_value = mock_response

    # Act
    result = service.create_budget(budget_id, max_budget, soft_budget, budget_duration)

    # Assert
    expected_headers = service._get_headers()
    mock_client.return_value.__enter__.return_value.post.assert_called_once_with(
        f"{config.LITE_LLM_URL}/budget/new",
        headers=expected_headers,
        json={
            "budget_id": budget_id,
            "max_budget": max_budget,
            "soft_budget": soft_budget,
            "budget_duration": budget_duration,
        },
    )

    assert isinstance(result, BudgetTable)
    assert result.budget_id == budget_id
    assert result.soft_budget == soft_budget
    assert result.max_budget == max_budget
    assert result.budget_duration == budget_duration


@patch("httpx.Client")
def test_create_budget_http_error(mock_client):
    """Test create_budget method when HTTP error occurs"""
    # Arrange
    service = LiteLLMService()

    mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPError("Test HTTP Error")

    # Act
    result = service.create_budget("test-budget-id", 200.0, 100.0)

    # Assert
    assert result is None


@patch("httpx.Client")
def test_create_customer_success(mock_client):
    """Test create_customer method success case"""
    # Arrange
    service = LiteLLMService()

    user_id = "test-user-id"
    budget_id = "test-budget-id"

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "user_id": user_id,
        "blocked": False,
        "spend": 0.0,
        "budget_id": budget_id,
        "litellm_budget_table": {
            "budget_id": budget_id,
            "soft_budget": 100.0,
            "max_budget": 200.0,
            "budget_duration": "30d",
        },
    }
    mock_client.return_value.__enter__.return_value.post.return_value = mock_response

    # Act
    result = service.create_customer(user_id, budget_id)

    # Assert
    expected_headers = service._get_headers()
    mock_client.return_value.__enter__.return_value.post.assert_called_once_with(
        f"{config.LITE_LLM_URL}/customer/new",
        headers=expected_headers,
        json={"user_id": user_id, "budget_id": budget_id},
    )

    assert isinstance(result, CustomerInfo)
    assert result.user_id == user_id
    assert result.budget_id == budget_id
    assert isinstance(result.litellm_budget_table, BudgetTable)
    assert result.litellm_budget_table.budget_id == budget_id


@patch("httpx.Client")
def test_create_customer_http_error(mock_client):
    """Test create_customer method when HTTP error occurs"""
    # Arrange
    service = LiteLLMService()

    mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPError("Test HTTP Error")

    # Act & Assert
    with pytest.raises(httpx.HTTPError):
        service.create_customer("test-user-id", "test-budget-id")


@patch("httpx.Client")
def test_get_customer_info_success(mock_client):
    """Test get_customer_info method success case"""
    # Arrange
    service = LiteLLMService()

    user_id = "test-user-id"
    budget_id = "test-budget-id"

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "user_id": user_id,
        "blocked": False,
        "spend": 0.0,
        "budget_id": budget_id,
        "litellm_budget_table": {
            "budget_id": budget_id,
            "soft_budget": 100.0,
            "max_budget": 200.0,
            "budget_duration": "30d",
        },
    }
    mock_client.return_value.__enter__.return_value.get.return_value = mock_response

    # Act
    result = service.get_customer_info(user_id)

    # Assert
    expected_headers = service._get_headers()
    mock_client.return_value.__enter__.return_value.get.assert_called_once_with(
        f"{config.LITE_LLM_URL}/customer/info", headers=expected_headers, params={"end_user_id": user_id}
    )

    assert isinstance(result, CustomerInfo)
    assert result.user_id == user_id
    assert result.budget_id == budget_id
    assert isinstance(result.litellm_budget_table, BudgetTable)


@patch("httpx.Client")
def test_get_customer_info_http_status_error(mock_client):
    """Test get_customer_info method when HTTPStatusError occurs (customer not found)"""
    # Arrange
    service = LiteLLMService()

    mock_client.return_value.__enter__.return_value.get.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=MagicMock()
    )

    # Act
    result = service.get_customer_info("test-user-id")

    # Assert
    assert result is None


@patch("httpx.Client")
def test_get_customer_info_general_http_error(mock_client):
    """Test get_customer_info method when general HTTPError occurs"""
    # Arrange
    service = LiteLLMService()

    mock_client.return_value.__enter__.return_value.get.side_effect = httpx.HTTPError("Test HTTP Error")

    # Act
    result = service.get_customer_info("test-user-id")

    # Assert
    assert result is None


@patch("httpx.Client")
def test_delete_customers_success(mock_client):
    """Test delete_customers method success case"""
    # Arrange
    service = LiteLLMService()

    user_ids = ["user1", "user2"]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client.return_value.__enter__.return_value.post.return_value = mock_response

    # Act
    result = service.delete_customers(user_ids)

    # Assert
    expected_headers = service._get_headers()
    mock_client.return_value.__enter__.return_value.post.assert_called_once_with(
        f"{config.LITE_LLM_URL}/customer/delete", headers=expected_headers, json={"user_ids": user_ids}
    )

    assert result is True


@patch("httpx.Client")
def test_delete_customers_failure(mock_client):
    """Test delete_customers method when API returns non-200 status"""
    # Arrange
    service = LiteLLMService()

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request"
    mock_client.return_value.__enter__.return_value.post.return_value = mock_response

    # Act
    result = service.delete_customers(["user1"])

    # Assert
    assert result is False


@patch("httpx.Client")
def test_delete_customers_http_error(mock_client):
    """Test delete_customers method when HTTP error occurs"""
    # Arrange
    service = LiteLLMService()

    mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPError("Test HTTP Error")

    # Act
    result = service.delete_customers(["user1"])

    # Assert
    assert result is False


class TestGetOrCreateBudget(unittest.TestCase):
    """Test suite for get_or_create_budget method"""

    def setUp(self):
        """Set up test fixtures"""
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "get_budget_info")
    def test_get_existing_budget(self, mock_get_budget_info):
        """Test get_or_create_budget when budget exists"""
        # Arrange
        budget_id = "existing-budget"
        existing_budget = BudgetTable(budget_id=budget_id, soft_budget=100.0, max_budget=200.0, budget_duration="30d")
        mock_get_budget_info.return_value = [existing_budget]

        # Act
        result = self.service.get_or_create_budget(budget_id)

        # Assert
        mock_get_budget_info.assert_called_once_with([budget_id])
        self.assertEqual(result, existing_budget)

    @patch.object(LiteLLMService, "create_budget")
    @patch.object(LiteLLMService, "get_budget_info")
    def test_create_new_budget(self, mock_get_budget_info, mock_create_budget):
        """Test get_or_create_budget when budget doesn't exist"""
        # Arrange
        budget_id = "new-budget"
        mock_get_budget_info.return_value = []

        new_budget = BudgetTable(budget_id=budget_id, soft_budget=100.0, max_budget=200.0, budget_duration="30d")
        mock_create_budget.return_value = new_budget

        # Act
        result = self.service.get_or_create_budget(budget_id)

        # Assert
        mock_get_budget_info.assert_called_once_with([budget_id])
        mock_create_budget.assert_called_once_with(
            budget_id=budget_id,
            max_budget=config.DEFAULT_HARD_BUDGET_LIMIT,
            budget_duration=config.DEFAULT_BUDGET_DURATION,
            soft_budget=config.DEFAULT_SOFT_BUDGET_LIMIT,
        )
        self.assertEqual(result, new_budget)

    @patch.object(LiteLLMService, "create_budget")
    @patch.object(LiteLLMService, "get_budget_info")
    def test_exception_handling(self, mock_get_budget_info, mock_create_budget):
        """Test get_or_create_budget exception handling"""
        # Arrange
        budget_id = "error-budget"
        mock_get_budget_info.side_effect = Exception("Test exception")

        # Act
        result = self.service.get_or_create_budget(budget_id)

        # Assert
        mock_get_budget_info.assert_called_once_with([budget_id])
        mock_create_budget.assert_not_called()
        self.assertIsNone(result)


class TestGetOrCreateCustomerWithBudget(unittest.TestCase):
    """Test suite for get_or_create_customer_with_budget method"""

    def setUp(self):
        """Set up test fixtures"""
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "get_customer_info")
    def test_existing_customer_with_budget(self, mock_get_customer_info):
        """Test get_or_create_customer_with_budget when customer exists with budget"""
        # Arrange
        user_id = "existing-user"
        budget_id = "existing-budget"

        budget_table = BudgetTable(budget_id=budget_id, soft_budget=100.0, max_budget=200.0, budget_duration="30d")

        existing_customer = CustomerInfo(user_id=user_id, budget_id=budget_id, litellm_budget_table=budget_table)

        mock_get_customer_info.return_value = existing_customer

        # Act
        result = self.service.get_or_create_customer_with_budget(user_id)

        # Assert
        mock_get_customer_info.assert_called_once_with(user_id)
        self.assertEqual(result, existing_customer)

    @patch.object(LiteLLMService, "create_customer")
    @patch.object(LiteLLMService, "get_or_create_budget")
    @patch.object(LiteLLMService, "get_customer_info")
    def test_non_existing_customer(self, mock_get_customer_info, mock_get_or_create_budget, mock_create_customer):
        """Test get_or_create_customer_with_budget when customer doesn't exist"""
        # Arrange
        user_id = "new-user"
        budget_id = config.DEFAULT_BUDGET_ID

        mock_get_customer_info.return_value = None

        budget_table = BudgetTable(budget_id=budget_id, soft_budget=100.0, max_budget=200.0, budget_duration="30d")

        mock_get_or_create_budget.return_value = budget_table

        new_customer = CustomerInfo(user_id=user_id, budget_id=budget_id, litellm_budget_table=budget_table)

        mock_create_customer.return_value = new_customer

        # Act
        result = self.service.get_or_create_customer_with_budget(user_id)

        # Assert
        mock_get_customer_info.assert_called_once_with(user_id)
        mock_get_or_create_budget.assert_called_once_with(budget_id)
        mock_create_customer.assert_called_once_with(user_id, budget_table.budget_id)
        self.assertEqual(result, new_customer)

    @patch.object(LiteLLMService, "create_customer")
    @patch.object(LiteLLMService, "delete_customers")
    @patch.object(LiteLLMService, "get_or_create_budget")
    @patch.object(LiteLLMService, "get_customer_info")
    def test_existing_customer_without_budget(
        self, mock_get_customer_info, mock_get_or_create_budget, mock_delete_customers, mock_create_customer
    ):
        """Test get_or_create_customer_with_budget when customer exists without budget"""
        # Arrange
        user_id = "existing-user-no-budget"
        budget_id = config.DEFAULT_BUDGET_ID

        existing_customer = CustomerInfo(user_id=user_id, budget_id=None, litellm_budget_table=None)

        mock_get_customer_info.return_value = existing_customer

        budget_table = BudgetTable(budget_id=budget_id, soft_budget=100.0, max_budget=200.0, budget_duration="30d")

        mock_get_or_create_budget.return_value = budget_table

        updated_customer = CustomerInfo(user_id=user_id, budget_id=budget_id, litellm_budget_table=budget_table)

        mock_create_customer.return_value = updated_customer

        # Act
        result = self.service.get_or_create_customer_with_budget(user_id)

        # Assert
        mock_get_customer_info.assert_called_once_with(user_id)
        mock_delete_customers.assert_called_once_with([user_id])
        mock_get_or_create_budget.assert_called_once_with(budget_id)
        mock_create_customer.assert_called_once_with(user_id, budget_id=budget_id)
        self.assertEqual(result, updated_customer)


@patch.object(LiteLLMService, "get_or_create_customer_with_budget")
def test_check_user_in_budget_success(mock_get_or_create_customer_with_budget):
    """Test check_user_in_budget successful execution"""
    # Arrange
    service = LiteLLMService()

    user_id = "test-user"

    budget_table = BudgetTable(budget_id="test-budget", soft_budget=100.0, max_budget=200.0, budget_duration="30d")

    customer = CustomerInfo(
        user_id=user_id,
        spend=50.0,  # Below both soft and hard limits
        budget_id="test-budget",
        litellm_budget_table=budget_table,
    )

    mock_get_or_create_customer_with_budget.return_value = customer

    # Act
    result = service.check_user_in_budget(user_id)

    # Assert
    mock_get_or_create_customer_with_budget.assert_called_once_with(user_id)
    assert result == customer


@patch.object(LiteLLMService, "get_or_create_customer_with_budget")
def test_check_user_in_budget_exceeds_soft_limit(mock_get_or_create_customer_with_budget, caplog):
    """Test check_user_in_budget when user exceeds soft limit"""
    # Arrange
    service = LiteLLMService()

    user_id = "test-user"

    budget_table = BudgetTable(budget_id="test-budget", soft_budget=100.0, max_budget=200.0, budget_duration="30d")

    customer = CustomerInfo(
        user_id=user_id,
        spend=150.0,  # Above soft limit but below hard limit
        budget_id="test-budget",
        litellm_budget_table=budget_table,
    )

    mock_get_or_create_customer_with_budget.return_value = customer

    # Act
    result = service.check_user_in_budget(user_id)

    # Assert
    mock_get_or_create_customer_with_budget.assert_called_once_with(user_id)
    assert result == customer
    # Would check for warning log here with caplog if running in pytest


@patch.object(LiteLLMService, "get_or_create_customer_with_budget")
def test_check_user_in_budget_exceeds_hard_limit(mock_get_or_create_customer_with_budget, caplog):
    """Test check_user_in_budget when user exceeds hard limit"""
    # Arrange
    service = LiteLLMService()

    user_id = "test-user"

    budget_table = BudgetTable(budget_id="test-budget", soft_budget=100.0, max_budget=200.0, budget_duration="30d")

    customer = CustomerInfo(
        user_id=user_id,
        spend=250.0,  # Above hard limit
        budget_id="test-budget",
        litellm_budget_table=budget_table,
    )

    mock_get_or_create_customer_with_budget.return_value = customer

    # Act
    result = service.check_user_in_budget(user_id)

    # Assert
    mock_get_or_create_customer_with_budget.assert_called_once_with(user_id)
    assert result == customer
    # Would check for warning log here with caplog if running in pytest


@patch.object(LiteLLMService, "get_or_create_customer_with_budget")
def test_check_user_in_budget_no_customer_created(mock_get_or_create_customer_with_budget, caplog):
    """Test check_user_in_budget when customer creation fails"""
    # Arrange
    service = LiteLLMService()

    user_id = "test-user"

    # Simulate failure to create or get customer
    mock_get_or_create_customer_with_budget.return_value = None

    # Act
    result = service.check_user_in_budget(user_id)

    # Assert
    mock_get_or_create_customer_with_budget.assert_called_once_with(user_id)
    assert result is None
    # Would check for error log here with caplog if running in pytest


@patch.object(LiteLLMService, "get_or_create_customer_with_budget")
def test_check_user_in_budget_exception(mock_get_or_create_customer_with_budget, caplog):
    """Test check_user_in_budget exception handling"""
    # Arrange
    service = LiteLLMService()

    user_id = "test-user"

    # Simulate exception
    mock_get_or_create_customer_with_budget.side_effect = Exception("Test exception")

    # Act
    result = service.check_user_in_budget(user_id)

    # Assert
    mock_get_or_create_customer_with_budget.assert_called_once_with(user_id)
    assert result is None
    # Would check for error log here with caplog if running in pytest


@patch.object(LiteLLMService, "get_or_create_customer_with_budget")
def test_check_user_in_budget_default_limits(mock_get_or_create_customer_with_budget):
    """Test check_user_in_budget with default limits when budget table is None"""
    service = LiteLLMService()

    user_id = "test-user"

    # Customer with no budget table
    customer = CustomerInfo(
        user_id=user_id,
        spend=150.0,  # Above default soft limit
        budget_id=None,
        litellm_budget_table=None,
    )

    mock_get_or_create_customer_with_budget.return_value = customer

    # Act
    result = service.check_user_in_budget(user_id)

    # Assert
    mock_get_or_create_customer_with_budget.assert_called_once_with(user_id)
    assert result == customer
    # Would check for warning log about soft limit if running in pytest


class TestEnsureDefaultBudget(unittest.TestCase):
    """Test suite for ensure_default_budget method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "create_budget")
    @patch.object(LiteLLMService, "get_budget_info")
    def test_ensure_default_budget_already_exists(self, mock_get_budget_info, mock_create_budget):
        """Test ensure_default_budget when budget already exists"""
        # Arrange
        budget_id = config.DEFAULT_BUDGET_ID
        existing_budget = BudgetTable(
            budget_id=budget_id,
            soft_budget=config.DEFAULT_SOFT_BUDGET_LIMIT,
            max_budget=config.DEFAULT_HARD_BUDGET_LIMIT,
            budget_duration=config.DEFAULT_BUDGET_DURATION,
        )
        mock_get_budget_info.return_value = [existing_budget]

        # Act
        self.service.ensure_default_budget()

        # Assert
        mock_get_budget_info.assert_called_once_with([budget_id])
        mock_create_budget.assert_not_called()

    @patch.object(LiteLLMService, "create_budget")
    @patch.object(LiteLLMService, "get_budget_info")
    def test_ensure_default_budget_creates_new(self, mock_get_budget_info, mock_create_budget):
        """Test ensure_default_budget creates budget when it doesn't exist"""
        # Arrange
        budget_id = config.DEFAULT_BUDGET_ID
        mock_get_budget_info.return_value = []  # No existing budget

        new_budget = BudgetTable(
            budget_id=budget_id,
            soft_budget=config.DEFAULT_SOFT_BUDGET_LIMIT,
            max_budget=config.DEFAULT_HARD_BUDGET_LIMIT,
            budget_duration=config.DEFAULT_BUDGET_DURATION,
        )
        mock_create_budget.return_value = new_budget

        # Act
        self.service.ensure_default_budget()

        # Assert
        mock_get_budget_info.assert_called_once_with([budget_id])
        mock_create_budget.assert_called_once_with(
            budget_id=budget_id,
            max_budget=config.DEFAULT_HARD_BUDGET_LIMIT,
            soft_budget=config.DEFAULT_SOFT_BUDGET_LIMIT,
            budget_duration=config.DEFAULT_BUDGET_DURATION,
        )

    @patch.object(LiteLLMService, "create_budget")
    @patch.object(LiteLLMService, "get_budget_info")
    def test_ensure_default_budget_creation_returns_none(self, mock_get_budget_info, mock_create_budget):
        """Test ensure_default_budget raises exception when budget creation returns None"""
        # Arrange
        budget_id = config.DEFAULT_BUDGET_ID
        mock_get_budget_info.return_value = []  # No existing budget
        mock_create_budget.return_value = None  # Creation failed

        # Act & Assert
        from codemie.core.exceptions import LiteLLMBudgetException

        with self.assertRaises(LiteLLMBudgetException) as context:
            self.service.ensure_default_budget()

        self.assertIn(f"Failed to create budget {budget_id}", str(context.exception.message))
        self.assertIn("Budget creation returned None from LiteLLM API", str(context.exception.details))

    @patch.object(LiteLLMService, "create_budget")
    @patch.object(LiteLLMService, "get_budget_info")
    def test_ensure_default_budget_get_budget_info_raises_exception(self, mock_get_budget_info, mock_create_budget):
        """Test ensure_default_budget handles exceptions from get_budget_info"""
        # Arrange
        budget_id = config.DEFAULT_BUDGET_ID
        mock_get_budget_info.side_effect = Exception("Network error")

        # Act & Assert
        from codemie.core.exceptions import LiteLLMBudgetException

        with self.assertRaises(LiteLLMBudgetException) as context:
            self.service.ensure_default_budget()

        self.assertIn(f"Failed to initialize default budget {budget_id}", str(context.exception.message))
        self.assertIn("Network error", str(context.exception.details))
        mock_create_budget.assert_not_called()

    @patch.object(LiteLLMService, "create_budget")
    @patch.object(LiteLLMService, "get_budget_info")
    def test_ensure_default_budget_create_budget_raises_exception(self, mock_get_budget_info, mock_create_budget):
        """Test ensure_default_budget handles exceptions from create_budget"""
        # Arrange
        budget_id = config.DEFAULT_BUDGET_ID
        mock_get_budget_info.return_value = []  # No existing budget
        mock_create_budget.side_effect = Exception("API error")

        # Act & Assert
        from codemie.core.exceptions import LiteLLMBudgetException

        with self.assertRaises(LiteLLMBudgetException) as context:
            self.service.ensure_default_budget()

        self.assertIn(f"Failed to initialize default budget {budget_id}", str(context.exception.message))
        self.assertIn("API error", str(context.exception.details))


class TestCreateCustomer(unittest.TestCase):
    """Test suite for _create_customer method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "create_customer")
    def test_create_customer_success(self, mock_create_customer):
        """Test _create_customer successfully creates customer"""
        # Arrange
        user_id = "test-user"
        budget_id = "test-budget"

        customer = CustomerInfo(
            user_id=user_id,
            budget_id=budget_id,
            litellm_budget_table=BudgetTable(
                budget_id=budget_id, soft_budget=100.0, max_budget=200.0, budget_duration="30d"
            ),
        )
        mock_create_customer.return_value = customer

        # Act
        result = self.service._create_customer(user_id, budget_id)

        # Assert
        mock_create_customer.assert_called_once_with(user_id, budget_id=budget_id)
        self.assertEqual(result, customer)

    @patch.object(LiteLLMService, "create_customer")
    def test_create_customer_503_error_fail_open_enabled(self, mock_create_customer):
        """Test _create_customer with 503 error when fail-open is enabled"""
        # Arrange
        user_id = "test-user"
        budget_id = "test-budget"

        mock_response = MagicMock()
        mock_response.status_code = 503
        http_error = httpx.HTTPStatusError("Service unavailable", request=MagicMock(), response=mock_response)
        mock_create_customer.side_effect = http_error

        # Act
        with patch.object(config, "LITELLM_FAIL_OPEN_ON_503", True):
            result = self.service._create_customer(user_id, budget_id)

        # Assert
        mock_create_customer.assert_called_once_with(user_id, budget_id=budget_id)
        self.assertIsNone(result)  # Fail open

    @patch.object(LiteLLMService, "create_customer")
    def test_create_customer_503_error_fail_open_disabled(self, mock_create_customer):
        """Test _create_customer with 503 error when fail-open is disabled"""
        # Arrange
        user_id = "test-user"
        budget_id = "test-budget"

        mock_response = MagicMock()
        mock_response.status_code = 503
        http_error = httpx.HTTPStatusError("Service unavailable", request=MagicMock(), response=mock_response)
        mock_create_customer.side_effect = http_error

        # Act & Assert
        with patch.object(config, "LITELLM_FAIL_OPEN_ON_503", False):
            with self.assertRaises(httpx.HTTPStatusError):
                self.service._create_customer(user_id, budget_id)

        mock_create_customer.assert_called_once_with(user_id, budget_id=budget_id)

    @patch.object(LiteLLMService, "create_customer")
    def test_create_customer_other_http_error(self, mock_create_customer):
        """Test _create_customer with non-503 HTTP error"""
        # Arrange
        user_id = "test-user"
        budget_id = "test-budget"

        mock_response = MagicMock()
        mock_response.status_code = 400
        http_error = httpx.HTTPStatusError("Bad request", request=MagicMock(), response=mock_response)
        mock_create_customer.side_effect = http_error

        # Act
        result = self.service._create_customer(user_id, budget_id)

        # Assert
        mock_create_customer.assert_called_once_with(user_id, budget_id=budget_id)
        self.assertIsNone(result)  # Returns None on error

    @patch.object(LiteLLMService, "create_customer")
    def test_create_customer_generic_exception(self, mock_create_customer):
        """Test _create_customer with generic exception"""
        # Arrange
        user_id = "test-user"
        budget_id = "test-budget"

        mock_create_customer.side_effect = Exception("Unexpected error")

        # Act
        result = self.service._create_customer(user_id, budget_id)

        # Assert
        mock_create_customer.assert_called_once_with(user_id, budget_id=budget_id)
        self.assertIsNone(result)  # Returns None on error


class TestGetHeaders(unittest.TestCase):
    """Test suite for _get_headers method"""

    def setUp(self):
        self.service = LiteLLMService()

    def test_get_headers_default_master_key(self):
        """Test _get_headers with default master key"""
        # Act
        headers = self.service._get_headers()

        # Assert
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], f"Bearer {config.LITE_LLM_MASTER_KEY}")

    def test_get_headers_custom_api_key(self):
        """Test _get_headers with custom API key"""
        # Arrange
        custom_key = "custom-api-key-123"

        # Act
        headers = self.service._get_headers(api_key=custom_key)

        # Assert
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], f"Bearer {custom_key}")


class TestGetAvailableModels(unittest.TestCase):
    """Test suite for get_available_models method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch("httpx.Client")
    def test_get_available_models_cache_hit(self, mock_client):
        """Test get_available_models returns cached models"""
        # Arrange
        user_id = "test-user"
        cached_models = LiteLLMModels(
            chat_models=[
                LLMModel(
                    base_name="gpt-4",
                    deployment_name="gpt-4",
                    label="GPT-4",
                    provider=LLMProvider.AZURE_OPENAI,
                    features=LLMFeatures(),
                    enabled=True,
                )
            ],
            embedding_models=[],
        )

        # Set cache
        cache_key = f"models:{user_id}"
        self.service.models_cache.set(cache_key, cached_models)

        # Act
        result = self.service.get_available_models(user_id=user_id)

        # Assert
        self.assertEqual(result, cached_models)
        mock_client.assert_not_called()

    @patch("httpx.Client")
    @patch.object(LiteLLMService, "_map_and_deduplicate_models")
    def test_get_available_models_cache_miss(self, mock_map_models, mock_client):
        """Test get_available_models fetches from LiteLLM on cache miss"""
        # Arrange
        user_id = "test-user"
        litellm_response = {
            "data": [
                {
                    "model_name": "gpt-4",
                    "model_info": {
                        "litellm_provider": "azure",
                        "supports_native_streaming": True,
                        "supports_function_calling": True,
                    },
                }
            ]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = litellm_response
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        expected_models = LiteLLMModels(
            chat_models=[
                LLMModel(
                    base_name="gpt-4",
                    deployment_name="gpt-4",
                    label="GPT-4",
                    provider=LLMProvider.AZURE_OPENAI,
                    features=LLMFeatures(),
                    enabled=True,
                )
            ],
            embedding_models=[],
        )
        mock_map_models.return_value = expected_models

        # Act
        result = self.service.get_available_models(user_id=user_id)

        # Assert
        mock_client.return_value.__enter__.return_value.get.assert_called_once()
        mock_map_models.assert_called_once_with(litellm_response["data"])
        self.assertEqual(result, expected_models)

        # Verify caching
        cache_key = f"models:{user_id}"
        cached_result = self.service.models_cache.get(cache_key)
        self.assertEqual(cached_result, expected_models)

    @patch("httpx.Client")
    def test_get_available_models_http_error(self, mock_client):
        """Test get_available_models handles HTTP errors gracefully"""
        # Arrange
        user_id = "test-user"
        mock_client.return_value.__enter__.return_value.get.side_effect = httpx.HTTPError("Network error")

        # Act
        result = self.service.get_available_models(user_id=user_id)

        # Assert
        self.assertEqual(result, LiteLLMModels())
        self.assertEqual(len(result.chat_models), 0)
        self.assertEqual(len(result.embedding_models), 0)


class TestMapAndDeduplicateModels(unittest.TestCase):
    """Test suite for _map_and_deduplicate_models method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "map_litellm_to_llm_model")
    def test_map_and_deduplicate_models_chat_only(self, mock_map_model):
        """Test _map_and_deduplicate_models with chat models only"""
        # Arrange
        litellm_models = [
            {"model_name": "gpt-4", "model_info": {"mode": "chat"}},
            {"model_name": "gpt-3.5-turbo", "model_info": {"mode": "chat"}},
        ]

        gpt4_model = LLMModel(
            base_name="gpt-4",
            deployment_name="gpt-4",
            label="GPT-4",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )
        gpt35_model = LLMModel(
            base_name="gpt-3.5-turbo",
            deployment_name="gpt-3.5-turbo",
            label="GPT-3.5",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )

        mock_map_model.side_effect = [gpt4_model, gpt35_model]

        # Act
        result = self.service._map_and_deduplicate_models(litellm_models)

        # Assert
        self.assertEqual(len(result.chat_models), 2)
        self.assertEqual(len(result.embedding_models), 0)
        self.assertEqual(result.chat_models[0].base_name, "gpt-4")
        self.assertEqual(result.chat_models[1].base_name, "gpt-3.5-turbo")

    @patch.object(LiteLLMService, "map_litellm_to_llm_model")
    def test_map_and_deduplicate_models_embedding_only(self, mock_map_model):
        """Test _map_and_deduplicate_models with embedding models only"""
        # Arrange
        litellm_models = [
            {"model_name": "text-embedding-ada-002", "model_info": {"mode": "embedding"}},
            {"model_name": "text-embedding-3-small", "model_info": {"mode": "embedding"}},
        ]

        ada_model = LLMModel(
            base_name="text-embedding-ada-002",
            deployment_name="text-embedding-ada-002",
            label="Ada Embedding",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )
        small_model = LLMModel(
            base_name="text-embedding-3-small",
            deployment_name="text-embedding-3-small",
            label="Small Embedding",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )

        mock_map_model.side_effect = [ada_model, small_model]

        # Act
        result = self.service._map_and_deduplicate_models(litellm_models)

        # Assert
        self.assertEqual(len(result.chat_models), 0)
        self.assertEqual(len(result.embedding_models), 2)
        self.assertEqual(result.embedding_models[0].base_name, "text-embedding-ada-002")
        self.assertEqual(result.embedding_models[1].base_name, "text-embedding-3-small")

    @patch.object(LiteLLMService, "map_litellm_to_llm_model")
    def test_map_and_deduplicate_models_mixed(self, mock_map_model):
        """Test _map_and_deduplicate_models with mixed chat and embedding models"""
        # Arrange
        litellm_models = [
            {"model_name": "gpt-4", "model_info": {"mode": "chat"}},
            {"model_name": "text-embedding-ada-002", "model_info": {"mode": "embedding"}},
        ]

        gpt4_model = LLMModel(
            base_name="gpt-4",
            deployment_name="gpt-4",
            label="GPT-4",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )
        ada_model = LLMModel(
            base_name="text-embedding-ada-002",
            deployment_name="text-embedding-ada-002",
            label="Ada Embedding",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )

        mock_map_model.side_effect = [gpt4_model, ada_model]

        # Act
        result = self.service._map_and_deduplicate_models(litellm_models)

        # Assert
        self.assertEqual(len(result.chat_models), 1)
        self.assertEqual(len(result.embedding_models), 1)
        self.assertEqual(result.chat_models[0].base_name, "gpt-4")
        self.assertEqual(result.embedding_models[0].base_name, "text-embedding-ada-002")

    @patch.object(LiteLLMService, "map_litellm_to_llm_model")
    def test_map_and_deduplicate_models_deduplication(self, mock_map_model):
        """Test _map_and_deduplicate_models removes duplicates by base_name"""
        # Arrange
        litellm_models = [
            {"model_name": "gpt-4", "model_info": {"mode": "chat"}},
            {"model_name": "gpt-4", "model_info": {"mode": "chat"}},
        ]

        gpt4_model = LLMModel(
            base_name="gpt-4",
            deployment_name="gpt-4",
            label="GPT-4",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )

        mock_map_model.side_effect = [gpt4_model, gpt4_model]

        # Act
        result = self.service._map_and_deduplicate_models(litellm_models)

        # Assert - should only have one model despite two inputs
        self.assertEqual(len(result.chat_models), 1)
        self.assertEqual(result.chat_models[0].base_name, "gpt-4")

    @patch.object(LiteLLMService, "map_litellm_to_llm_model")
    def test_map_and_deduplicate_models_handles_mapping_error(self, mock_map_model):
        """Test _map_and_deduplicate_models handles mapping errors gracefully"""
        # Arrange
        litellm_models = [
            {"model_name": "gpt-4", "model_info": {"mode": "chat"}},
            {"model_name": "invalid-model", "model_info": {"mode": "chat"}},
        ]

        gpt4_model = LLMModel(
            base_name="gpt-4",
            deployment_name="gpt-4",
            label="GPT-4",
            provider=LLMProvider.AZURE_OPENAI,
            features=LLMFeatures(),
            enabled=True,
        )

        # First call succeeds, second fails
        mock_map_model.side_effect = [gpt4_model, Exception("Mapping error")]

        # Act
        result = self.service._map_and_deduplicate_models(litellm_models)

        # Assert - should only have the successful model
        self.assertEqual(len(result.chat_models), 1)
        self.assertEqual(result.chat_models[0].base_name, "gpt-4")


class TestMapLiteLLMToLLMModel(unittest.TestCase):
    """Test suite for map_litellm_to_llm_model method"""

    def setUp(self):
        self.service = LiteLLMService()

    def test_map_litellm_to_llm_model_complete(self):
        """Test map_litellm_to_llm_model with complete model info"""
        # Arrange
        litellm_model = {
            "model_name": "gpt-4",
            "model_info": {
                "id": "gpt-4",
                "label": "GPT-4 Model",
                "litellm_provider": "azure",
                "supports_native_streaming": True,
                "supports_function_calling": True,
                "supports_system_messages": True,
                "supports_vision": False,
                "supported_openai_params": ["temperature", "max_tokens", "top_p", "parallel_tool_calls"],
                "input_cost_per_token": 0.00003,
                "output_cost_per_token": 0.00006,
                "default_for_categories": ["global", "fast"],
                "mode": "chat",
            },
        }

        # Act
        result = self.service.map_litellm_to_llm_model(litellm_model)

        # Assert
        self.assertEqual(result.base_name, "gpt-4")
        self.assertEqual(result.deployment_name, "gpt-4")
        self.assertEqual(result.label, "GPT-4 Model")
        self.assertEqual(result.provider, LLMProvider.AZURE_OPENAI)
        self.assertTrue(result.features.streaming)
        self.assertTrue(result.features.tools)
        self.assertTrue(result.features.system_prompt)
        self.assertTrue(result.features.parallel_tool_calls)
        self.assertTrue(result.features.temperature)
        self.assertTrue(result.features.max_tokens)
        self.assertTrue(result.features.top_p)
        self.assertFalse(result.multimodal)
        # Model supports function calling, so should NOT use react agent
        self.assertFalse(result.react_agent)
        self.assertIsNotNone(result.cost)
        self.assertEqual(result.cost.input, 0.00003)
        self.assertEqual(result.cost.output, 0.00006)
        self.assertTrue(result.default)
        self.assertIn(ModelCategory.GLOBAL, result.default_for_categories)

    def test_map_litellm_to_llm_model_minimal(self):
        """Test map_litellm_to_llm_model with minimal model info"""
        # Arrange
        litellm_model = {
            "model_name": "simple-model",
            "model_info": {},
        }

        # Act
        result = self.service.map_litellm_to_llm_model(litellm_model)

        # Assert
        self.assertEqual(result.base_name, "simple-model")
        self.assertEqual(result.deployment_name, "simple-model")
        self.assertEqual(result.label, "simple-model")
        self.assertEqual(result.provider, LLMProvider.AZURE_OPENAI)  # Default provider
        self.assertTrue(result.features.streaming)  # Default True
        self.assertFalse(result.features.tools)  # Default False
        self.assertTrue(result.features.system_prompt)  # Default True
        self.assertFalse(result.multimodal)
        # Model does NOT support function calling, so SHOULD use react agent
        self.assertTrue(result.react_agent)
        self.assertIsNone(result.cost)
        self.assertFalse(result.default)

    def test_map_litellm_to_llm_model_bedrock_provider(self):
        """Test map_litellm_to_llm_model with Bedrock provider"""
        # Arrange
        litellm_model = {
            "model_name": "anthropic.claude-v2",
            "model_info": {
                "litellm_provider": "bedrock",
            },
        }

        # Act
        result = self.service.map_litellm_to_llm_model(litellm_model)

        # Assert
        self.assertEqual(result.provider, LLMProvider.AWS_BEDROCK)

    def test_map_litellm_to_llm_model_vertex_provider(self):
        """Test map_litellm_to_llm_model with Vertex AI provider"""
        # Arrange
        litellm_model = {
            "model_name": "gemini-pro",
            "model_info": {
                "litellm_provider": "vertex_ai",
            },
        }

        # Act
        result = self.service.map_litellm_to_llm_model(litellm_model)

        # Assert
        self.assertEqual(result.provider, LLMProvider.GOOGLE_VERTEX_AI)

    def test_map_litellm_to_llm_model_multimodal(self):
        """Test map_litellm_to_llm_model with multimodal model"""
        # Arrange
        litellm_model = {
            "model_name": "gpt-4-vision",
            "model_info": {
                "supports_vision": True,
            },
        }

        # Act
        result = self.service.map_litellm_to_llm_model(litellm_model)

        # Assert
        self.assertTrue(result.multimodal)


class TestGetUserAllowedModels(unittest.TestCase):
    """Test suite for get_user_allowed_models method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "get_available_models")
    @patch.object(LiteLLMService, "_get_litellm_credentials_for_user")
    def test_get_user_allowed_models_cache_hit(self, mock_get_creds, mock_get_models):
        """Test get_user_allowed_models returns cached models"""
        # Arrange
        user_id = "test-user"
        user_apps = ["app1"]

        cached_models = LiteLLMModels(
            chat_models=[
                LLMModel(
                    base_name="gpt-4",
                    deployment_name="gpt-4",
                    label="GPT-4",
                    provider=LLMProvider.AZURE_OPENAI,
                    features=LLMFeatures(),
                    enabled=True,
                )
            ],
            embedding_models=[],
        )

        cache_key = f"models:{user_id}"
        self.service.models_cache.set(cache_key, cached_models)

        # Act
        result = self.service.get_user_allowed_models(user_id, user_apps)

        # Assert
        self.assertEqual(result, cached_models)
        mock_get_creds.assert_not_called()
        mock_get_models.assert_not_called()

    @patch.object(LiteLLMService, "_map_and_deduplicate_models")
    @patch.object(LiteLLMService, "get_available_models")
    @patch.object(LiteLLMService, "_get_litellm_credentials_for_user")
    def test_get_user_allowed_models_no_credentials(self, mock_get_creds, mock_get_models, mock_map_models):
        """Test get_user_allowed_models when user has no LiteLLM credentials"""
        # Arrange
        user_id = "test-user"
        user_apps = ["app1"]

        mock_get_creds.return_value = None

        # Act
        result = self.service.get_user_allowed_models(user_id, user_apps)

        # Assert
        self.assertIsNone(result)
        mock_get_creds.assert_called_once_with(user_id, user_apps)
        mock_get_models.assert_not_called()
        mock_map_models.assert_not_called()

    @patch.object(LiteLLMService, "get_available_models")
    @patch.object(LiteLLMService, "_get_litellm_credentials_for_user")
    def test_get_user_allowed_models_fetch_success(self, mock_get_creds, mock_get_models):
        """Test get_user_allowed_models successfully fetches and caches models"""
        # Arrange
        user_id = "test-user"
        user_apps = ["app1"]

        mock_creds = Mock()
        mock_creds.api_key = "user-api-key"
        mock_get_creds.return_value = mock_creds

        expected_models = LiteLLMModels(
            chat_models=[
                LLMModel(
                    base_name="gpt-4",
                    deployment_name="gpt-4",
                    label="GPT-4",
                    provider=LLMProvider.AZURE_OPENAI,
                    features=LLMFeatures(),
                    enabled=True,
                )
            ],
            embedding_models=[],
        )
        mock_get_models.return_value = expected_models

        # Act
        result = self.service.get_user_allowed_models(user_id, user_apps)

        # Assert
        self.assertEqual(result, expected_models)
        mock_get_creds.assert_called_once_with(user_id, user_apps)
        mock_get_models.assert_called_once_with(user_id=user_id, api_key="user-api-key")

        # Verify caching
        cache_key = f"models:{user_id}"
        cached_result = self.service.models_cache.get(cache_key)
        self.assertEqual(cached_result, expected_models)


class TestGetLiteLLMCredentialsForUser(unittest.TestCase):
    """Test suite for _get_litellm_credentials_for_user method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch("codemie.service.settings.settings.SettingsService")
    def test_get_litellm_credentials_user_level(self, mock_settings_service):
        """Test _get_litellm_credentials_for_user finds user-level credentials"""
        # Arrange
        user_id = "test-user"
        user_apps = ["app1", "app2"]

        mock_creds = Mock()
        mock_creds.api_key = "user-api-key"
        mock_settings_service.get_litellm_creds.return_value = mock_creds

        # Act
        result = self.service._get_litellm_credentials_for_user(user_id, user_apps)

        # Assert
        self.assertEqual(result, mock_creds)
        # Should check user-level first
        mock_settings_service.get_litellm_creds.assert_called_with(project_name=None, user_id=user_id)

    @patch("codemie.service.settings.settings.SettingsService")
    def test_get_litellm_credentials_project_level(self, mock_settings_service):
        """Test _get_litellm_credentials_for_user finds project-level credentials"""
        # Arrange
        user_id = "test-user"
        user_apps = ["app1", "app2"]

        # User-level returns None, project-level returns credentials
        def side_effect(project_name, user_id):
            if project_name is None:
                return None
            elif project_name == "app1":
                mock_creds = Mock()
                mock_creds.api_key = "app1-api-key"
                return mock_creds
            return None

        mock_settings_service.get_litellm_creds.side_effect = side_effect

        # Act
        result = self.service._get_litellm_credentials_for_user(user_id, user_apps)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(result.api_key, "app1-api-key")

    @patch("codemie.service.settings.settings.SettingsService")
    def test_get_litellm_credentials_none_found(self, mock_settings_service):
        """Test _get_litellm_credentials_for_user when no credentials found"""
        # Arrange
        user_id = "test-user"
        user_apps = ["app1", "app2"]

        mock_settings_service.get_litellm_creds.return_value = None

        # Act
        result = self.service._get_litellm_credentials_for_user(user_id, user_apps)

        # Assert
        self.assertIsNone(result)


class TestCleanExpiredModelsCache(unittest.TestCase):
    """Test suite for clean_expired_models_cache method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(CacheManager, "clean_expired")
    @patch.object(CacheManager, "size")
    def test_clean_expired_models_cache_with_expired_entries(self, mock_size, mock_clean_expired):
        """Test clean_expired_models_cache removes expired entries"""
        # Arrange
        mock_size.side_effect = [10, 7]  # Before and after cleanup
        mock_clean_expired.return_value = 3  # 3 expired entries removed

        # Act
        result = self.service.clean_expired_models_cache()

        # Assert
        self.assertEqual(result, 3)
        mock_clean_expired.assert_called_once()

    @patch.object(CacheManager, "clean_expired")
    @patch.object(CacheManager, "size")
    def test_clean_expired_models_cache_no_expired_entries(self, mock_size, mock_clean_expired):
        """Test clean_expired_models_cache when no expired entries"""
        # Arrange
        mock_size.return_value = 5
        mock_clean_expired.return_value = 0  # No expired entries

        # Act
        result = self.service.clean_expired_models_cache()

        # Assert
        self.assertEqual(result, 0)
        mock_clean_expired.assert_called_once()


class TestGetCustomerSpending(unittest.TestCase):
    """Test suite for get_customer_spending method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "get_budget_info")
    @patch.object(LiteLLMService, "get_customer_info")
    def test_get_customer_spending_success(self, mock_get_customer, mock_get_budget):
        """Test get_customer_spending returns spending info successfully"""
        # Arrange
        user_id = "test-user"
        budget_id = "test-budget"

        budget_table = BudgetTable(
            budget_id=budget_id,
            soft_budget=100.0,
            max_budget=200.0,
            budget_duration="30d",
            budget_reset_at="2025-01-01T00:00:00Z",
        )

        customer = CustomerInfo(
            user_id=user_id,
            spend=50.0,
            budget_id=budget_id,
            litellm_budget_table=budget_table,
        )

        mock_get_customer.return_value = customer
        mock_get_budget.return_value = [budget_table]

        # Act
        result = self.service.get_customer_spending(user_id)

        # Assert
        mock_get_customer.assert_called_once_with(user_id)
        mock_get_budget.assert_called_once_with([budget_id])
        self.assertIsNotNone(result)
        self.assertEqual(result["customer_id"], user_id)
        self.assertEqual(result["total_spend"], 50.0)
        self.assertEqual(result["max_budget"], 200.0)
        self.assertEqual(result["budget_duration"], "30d")
        self.assertEqual(result["budget_reset_at"], "2025-01-01T00:00:00Z")

    @patch.object(LiteLLMService, "get_customer_info")
    def test_get_customer_spending_no_customer(self, mock_get_customer):
        """Test get_customer_spending returns None when customer doesn't exist"""
        # Arrange
        user_id = "non-existent-user"
        mock_get_customer.return_value = None

        # Act
        result = self.service.get_customer_spending(user_id)

        # Assert
        mock_get_customer.assert_called_once_with(user_id)
        self.assertIsNone(result)

    @patch.object(LiteLLMService, "get_budget_info")
    @patch.object(LiteLLMService, "get_customer_info")
    def test_get_customer_spending_no_budget_table(self, mock_get_customer, mock_get_budget):
        """Test get_customer_spending with customer but no budget table"""
        # Arrange
        user_id = "test-user"

        customer = CustomerInfo(
            user_id=user_id,
            spend=50.0,
            budget_id=None,
            litellm_budget_table=None,
        )

        mock_get_customer.return_value = customer

        # Act
        result = self.service.get_customer_spending(user_id)

        # Assert
        mock_get_customer.assert_called_once_with(user_id)
        mock_get_budget.assert_not_called()  # Should not call get_budget_info when no budget table
        self.assertIsNotNone(result)
        self.assertEqual(result["customer_id"], user_id)
        self.assertEqual(result["total_spend"], 50.0)
        self.assertIsNone(result["max_budget"])  # No budget table means None
        self.assertIsNone(result["budget_duration"])  # No budget table means None
        self.assertIsNone(result["budget_reset_at"])  # No budget table means None

    @patch.object(LiteLLMService, "get_budget_info")
    @patch.object(LiteLLMService, "get_customer_info")
    def test_get_customer_spending_empty_budget_list(self, mock_get_customer, mock_get_budget):
        """Test get_customer_spending when budget_info returns empty list"""
        # Arrange
        user_id = "test-user"
        budget_id = "test-budget"

        budget_table = BudgetTable(
            budget_id=budget_id,
            soft_budget=100.0,
            max_budget=200.0,
            budget_duration="30d",
        )

        customer = CustomerInfo(
            user_id=user_id,
            spend=50.0,
            budget_id=budget_id,
            litellm_budget_table=budget_table,
        )

        mock_get_customer.return_value = customer
        mock_get_budget.return_value = []  # Empty budget list

        # Act
        result = self.service.get_customer_spending(user_id)

        # Assert
        mock_get_customer.assert_called_once_with(user_id)
        mock_get_budget.assert_called_once_with([budget_id])
        self.assertIsNotNone(result)
        self.assertEqual(result["customer_id"], user_id)
        self.assertEqual(result["total_spend"], 50.0)
        self.assertEqual(result["max_budget"], 200.0)
        self.assertEqual(result["budget_duration"], "30d")
        self.assertIsNone(result["budget_reset_at"])  # Should be None when budgets list is empty


class TestGetKeyInfo(unittest.TestCase):
    """Test suite for get_key_info method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_success(self, mock_fetch_keys):
        """Test get_key_info returns filtered keys successfully"""
        # Arrange
        key_aliases = ["key1", "key2"]
        all_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),
            KeySpendingInfo(key_alias="key2", spend=200.0),
            KeySpendingInfo(key_alias="key3", spend=300.0),
        ]
        mock_fetch_keys.return_value = all_keys

        # Act
        result = self.service.get_key_info(key_aliases, include_details=True, page=1, size=100)

        # Assert
        mock_fetch_keys.assert_called_once_with(page=1, size=100, return_full_object=True)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].key_alias, "key1")
        self.assertEqual(result[1].key_alias, "key2")

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_empty_key_aliases(self, mock_fetch_keys):
        """Test get_key_info returns empty list for empty key_aliases"""
        # Arrange
        key_aliases = []

        # Act
        result = self.service.get_key_info(key_aliases)

        # Assert
        mock_fetch_keys.assert_not_called()
        self.assertEqual(result, [])

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_no_matching_keys(self, mock_fetch_keys):
        """Test get_key_info when no keys match the aliases"""
        # Arrange
        key_aliases = ["non-existent-key"]
        all_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),
            KeySpendingInfo(key_alias="key2", spend=200.0),
        ]
        mock_fetch_keys.return_value = all_keys

        # Act
        result = self.service.get_key_info(key_aliases)

        # Assert
        mock_fetch_keys.assert_called_once()
        self.assertEqual(len(result), 0)


class TestGetAllKeysSpending(unittest.TestCase):
    """Test suite for get_all_keys_spending method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_all_keys_spending_success(self, mock_fetch_keys):
        """Test get_all_keys_spending returns all keys"""
        # Arrange
        expected_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),
            KeySpendingInfo(key_alias="key2", spend=200.0),
        ]
        mock_fetch_keys.return_value = expected_keys

        # Act
        result = self.service.get_all_keys_spending(include_details=True, page=1, size=50)

        # Assert
        mock_fetch_keys.assert_called_once_with(page=1, size=50, return_full_object=True)
        self.assertEqual(result, expected_keys)


@patch("httpx.Client")
def test_fetch_keys_page_success(mock_client):
    """Test _fetch_keys_page successfully fetches keys"""
    # Arrange
    service = LiteLLMService()

    keys_data = [
        {"key_alias": "key1", "spend": 100.0, "max_budget": 1000.0},
        {"key_alias": "key2", "spend": 200.0, "max_budget": 2000.0},
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = {"keys": keys_data}
    mock_client.return_value.__enter__.return_value.get.return_value = mock_response

    # Act
    result = service._fetch_keys_page(page=1, size=100, return_full_object=True)

    # Assert
    mock_client.assert_called_once_with(timeout=DEFAULT_REQUEST_TIMEOUT)
    expected_headers = service._get_headers()
    expected_params = {
        "page": 1,
        "size": 100,
        "return_full_object": "true",
        "include_team_keys": "false",
        "include_created_by_keys": "false",
        "sort_order": "desc",
    }
    mock_client.return_value.__enter__.return_value.get.assert_called_once_with(
        f"{config.LITE_LLM_URL}/key/list", headers=expected_headers, params=expected_params
    )
    assert len(result) == 2
    assert isinstance(result[0], KeySpendingInfo)
    assert result[0].key_alias == "key1"
    assert result[0].spend == 100.0


@patch("httpx.Client")
def test_fetch_keys_page_response_as_list(mock_client):
    """Test _fetch_keys_page when response is a list instead of dict"""
    # Arrange
    service = LiteLLMService()

    keys_data = [
        {"key_alias": "key1", "spend": 100.0},
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = keys_data  # Direct list, not wrapped in dict
    mock_client.return_value.__enter__.return_value.get.return_value = mock_response

    # Act
    result = service._fetch_keys_page()

    # Assert
    assert len(result) == 1
    assert result[0].key_alias == "key1"


@patch("httpx.Client")
def test_fetch_keys_page_http_error(mock_client):
    """Test _fetch_keys_page handles HTTP errors gracefully"""
    # Arrange
    service = LiteLLMService()
    mock_client.return_value.__enter__.return_value.get.side_effect = httpx.HTTPError("Network error")

    # Act
    result = service._fetch_keys_page()

    # Assert
    assert result == []


@patch("httpx.Client")
def test_fetch_keys_page_generic_exception(mock_client):
    """Test _fetch_keys_page handles generic exceptions"""
    # Arrange
    service = LiteLLMService()
    mock_client.return_value.__enter__.return_value.get.side_effect = Exception("Unexpected error")

    # Act
    result = service._fetch_keys_page()

    # Assert
    assert result == []


@patch("httpx.Client")
def test_fetch_keys_page_with_custom_params(mock_client):
    """Test _fetch_keys_page with custom parameters

    Note: After bug fix, _fetch_keys_page ALWAYS sends return_full_object='true'
    to ensure we get spending data, regardless of the parameter value.
    """
    # Arrange
    service = LiteLLMService()

    mock_response = MagicMock()
    mock_response.json.return_value = {"keys": []}
    mock_client.return_value.__enter__.return_value.get.return_value = mock_response

    # Act
    result = service._fetch_keys_page(
        page=2,
        size=50,
        return_full_object=False,  # This parameter is ignored
        include_team_keys=True,
        include_created_by_keys=True,
        sort_order="asc",
    )

    # Assert
    # After the fix, return_full_object is always 'true' to ensure we get full data
    expected_params = {
        "page": 2,
        "size": 50,
        "return_full_object": "true",  # Always true now
        "include_team_keys": "true",
        "include_created_by_keys": "true",
        "sort_order": "asc",
    }
    mock_client.return_value.__enter__.return_value.get.assert_called_once()
    call_args = mock_client.return_value.__enter__.return_value.get.call_args
    assert call_args[1]["params"] == expected_params
    assert result == []


# ===========================
# Additional comprehensive tests for key-related functions
# ===========================


class TestGetKeyInfoComprehensive(unittest.TestCase):
    """Comprehensive test suite for get_key_info method covering additional scenarios"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_with_include_details_false(self, mock_fetch_keys):
        """Test get_key_info with include_details=False parameter"""
        # Arrange
        key_aliases = ["key1"]
        all_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0, key_name="Test Key 1"),
            KeySpendingInfo(key_alias="key2", spend=200.0, key_name="Test Key 2"),
        ]
        mock_fetch_keys.return_value = all_keys

        # Act
        result = self.service.get_key_info(key_aliases, include_details=False, page=1, size=100)

        # Assert
        mock_fetch_keys.assert_called_once_with(page=1, size=100, return_full_object=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].key_alias, "key1")

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_with_custom_pagination(self, mock_fetch_keys):
        """Test get_key_info with custom page and size parameters"""
        # Arrange
        key_aliases = ["key1", "key2"]
        all_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),
            KeySpendingInfo(key_alias="key2", spend=200.0),
        ]
        mock_fetch_keys.return_value = all_keys

        # Act
        result = self.service.get_key_info(key_aliases, include_details=True, page=2, size=50)

        # Assert
        mock_fetch_keys.assert_called_once_with(page=2, size=50, return_full_object=True)
        self.assertEqual(len(result), 2)

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_with_duplicate_key_aliases(self, mock_fetch_keys):
        """Test get_key_info handles duplicate key aliases in input"""
        # Arrange
        key_aliases = ["key1", "key1", "key2", "key2"]  # Duplicates
        all_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),
            KeySpendingInfo(key_alias="key2", spend=200.0),
            KeySpendingInfo(key_alias="key3", spend=300.0),
        ]
        mock_fetch_keys.return_value = all_keys

        # Act
        result = self.service.get_key_info(key_aliases)

        # Assert
        mock_fetch_keys.assert_called_once()
        # Should return unique keys even with duplicate aliases in input
        self.assertEqual(len(result), 2)
        result_aliases = {key.key_alias for key in result}
        self.assertEqual(result_aliases, {"key1", "key2"})

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_filters_none_key_alias(self, mock_fetch_keys):
        """Test get_key_info handles keys with None key_alias"""
        # Arrange
        key_aliases = ["key1", "key2"]
        all_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),
            KeySpendingInfo(key_alias=None, spend=150.0),  # None key_alias
            KeySpendingInfo(key_alias="key2", spend=200.0),
        ]
        mock_fetch_keys.return_value = all_keys

        # Act
        result = self.service.get_key_info(key_aliases)

        # Assert
        mock_fetch_keys.assert_called_once()
        # Should only return keys that match the provided aliases
        self.assertEqual(len(result), 2)
        self.assertNotIn(None, [key.key_alias for key in result])

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_with_case_sensitive_matching(self, mock_fetch_keys):
        """Test get_key_info performs case-sensitive key alias matching"""
        # Arrange
        key_aliases = ["Key1", "key2"]  # Mixed case
        all_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),  # lowercase
            KeySpendingInfo(key_alias="Key1", spend=150.0),  # capital K
            KeySpendingInfo(key_alias="key2", spend=200.0),
        ]
        mock_fetch_keys.return_value = all_keys

        # Act
        result = self.service.get_key_info(key_aliases)

        # Assert
        mock_fetch_keys.assert_called_once()
        # Should only match exact case
        self.assertEqual(len(result), 2)
        result_aliases = {key.key_alias for key in result}
        self.assertEqual(result_aliases, {"Key1", "key2"})

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_key_info_with_empty_fetch_result(self, mock_fetch_keys):
        """Test get_key_info when _fetch_keys_page returns empty list"""
        # Arrange
        key_aliases = ["key1", "key2"]
        mock_fetch_keys.return_value = []

        # Act
        result = self.service.get_key_info(key_aliases)

        # Assert
        mock_fetch_keys.assert_called_once()
        self.assertEqual(result, [])


class TestGetAllKeysSpendingComprehensive(unittest.TestCase):
    """Comprehensive test suite for get_all_keys_spending method"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_all_keys_spending_with_include_details_false(self, mock_fetch_keys):
        """Test get_all_keys_spending with include_details=False"""
        # Arrange
        expected_keys = [
            KeySpendingInfo(key_alias="key1", spend=100.0),
            KeySpendingInfo(key_alias="key2", spend=200.0),
        ]
        mock_fetch_keys.return_value = expected_keys

        # Act
        result = self.service.get_all_keys_spending(include_details=False, page=1, size=100)

        # Assert
        mock_fetch_keys.assert_called_once_with(page=1, size=100, return_full_object=False)
        self.assertEqual(result, expected_keys)

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_all_keys_spending_with_custom_pagination(self, mock_fetch_keys):
        """Test get_all_keys_spending with custom page and size"""
        # Arrange
        expected_keys = [KeySpendingInfo(key_alias="key1", spend=100.0)]
        mock_fetch_keys.return_value = expected_keys

        # Act
        result = self.service.get_all_keys_spending(include_details=True, page=3, size=25)

        # Assert
        mock_fetch_keys.assert_called_once_with(page=3, size=25, return_full_object=True)
        self.assertEqual(result, expected_keys)

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_all_keys_spending_with_empty_result(self, mock_fetch_keys):
        """Test get_all_keys_spending when no keys exist"""
        # Arrange
        mock_fetch_keys.return_value = []

        # Act
        result = self.service.get_all_keys_spending()

        # Assert
        mock_fetch_keys.assert_called_once()
        self.assertEqual(result, [])

    @patch.object(LiteLLMService, "_fetch_keys_page")
    def test_get_all_keys_spending_with_default_parameters(self, mock_fetch_keys):
        """Test get_all_keys_spending uses correct default parameters"""
        # Arrange
        expected_keys = [KeySpendingInfo(key_alias="key1", spend=100.0)]
        mock_fetch_keys.return_value = expected_keys

        # Act
        result = self.service.get_all_keys_spending()

        # Assert
        mock_fetch_keys.assert_called_once_with(page=1, size=100, return_full_object=True)
        self.assertEqual(result, expected_keys)


class TestFetchKeysPageComprehensive(unittest.TestCase):
    """Comprehensive test suite for _fetch_keys_page method covering edge cases"""

    def setUp(self):
        self.service = LiteLLMService()

    @patch("httpx.Client")
    def test_fetch_keys_page_filters_none_key_data(self, mock_client):
        """Test _fetch_keys_page filters out None entries from keys data"""
        # Arrange
        keys_data = [
            {"key_alias": "key1", "spend": 100.0},
            None,  # None entry should be filtered
            {"key_alias": "key2", "spend": 200.0},
            None,  # Another None entry
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": keys_data}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert len(result) == 2
        assert result[0].key_alias == "key1"
        assert result[1].key_alias == "key2"

    @patch("httpx.Client")
    def test_fetch_keys_page_filters_empty_dict_key_data(self, mock_client):
        """Test _fetch_keys_page filters out empty dict entries"""
        # Arrange
        keys_data = [
            {"key_alias": "key1", "spend": 100.0},
            {},  # Empty dict should be filtered
            {"key_alias": "key2", "spend": 200.0},
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": keys_data}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert len(result) == 2
        assert result[0].key_alias == "key1"
        assert result[1].key_alias == "key2"

    @patch("httpx.Client")
    def test_fetch_keys_page_http_status_error(self, mock_client):
        """Test _fetch_keys_page handles HTTPStatusError gracefully"""
        # Arrange
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        )

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert result == []

    @patch("httpx.Client")
    def test_fetch_keys_page_with_missing_keys_field(self, mock_client):
        """Test _fetch_keys_page when response dict has no 'keys' field"""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [], "total": 0}  # No 'keys' field

        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        # Should return empty list when 'keys' field is missing
        assert result == []

    @patch("httpx.Client")
    def test_fetch_keys_page_with_all_parameters(self, mock_client):
        """Test _fetch_keys_page with all possible parameters"""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": [{"key_alias": "key1", "spend": 100.0}]}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        result = self.service._fetch_keys_page(
            page=5,
            size=25,
            return_full_object=True,
            include_team_keys=True,
            include_created_by_keys=True,
            sort_order="asc",
        )

        # Assert
        expected_params = {
            "page": 5,
            "size": 25,
            "return_full_object": "true",
            "include_team_keys": "true",
            "include_created_by_keys": "true",
            "sort_order": "asc",
        }
        call_args = mock_client.return_value.__enter__.return_value.get.call_args
        assert call_args[1]["params"] == expected_params
        assert len(result) == 1
        assert result[0].key_alias == "key1"

    @patch("httpx.Client")
    def test_fetch_keys_page_timeout_error(self, mock_client):
        """Test _fetch_keys_page handles timeout errors gracefully"""
        # Arrange
        mock_client.return_value.__enter__.return_value.get.side_effect = httpx.TimeoutException("Request timed out")

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert result == []

    @patch("httpx.Client")
    def test_fetch_keys_page_connect_error(self, mock_client):
        """Test _fetch_keys_page handles connection errors gracefully"""
        # Arrange
        mock_client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("Connection failed")

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert result == []

    @patch("httpx.Client")
    def test_fetch_keys_page_request_error(self, mock_client):
        """Test _fetch_keys_page handles request errors gracefully"""
        # Arrange
        mock_client.return_value.__enter__.return_value.get.side_effect = httpx.RequestError("Request error")

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert result == []

    @patch("httpx.Client")
    def test_fetch_keys_page_json_decode_error(self, mock_client):
        """Test _fetch_keys_page handles JSON decode errors"""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert result == []

    @patch("httpx.Client")
    def test_fetch_keys_page_with_complex_key_data(self, mock_client):
        """Test _fetch_keys_page with complex KeySpendingInfo data"""
        # Arrange
        keys_data = [
            {
                "key_alias": "production-key-1",
                "key_name": "Production Key 1",
                "spend": 1234.56,
                "max_budget": 5000.0,
                "budget_duration": "30d",
                "budget_reset_at": "2025-02-01T00:00:00Z",
                "models": ["gpt-4", "gpt-3.5-turbo"],
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-15T10:30:00Z",
                "last_refreshed_at": "2025-01-20T14:45:00Z",
                "team_id": "team-123",
                "user_id": "user-456",
                "metadata": {"environment": "production", "department": "engineering"},
                "expires": "2025-12-31T23:59:59Z",
                "tpm_limit": 10000,
                "rpm_limit": 100,
                "max_parallel_requests": 5,
                "blocked": False,
                "soft_budget_cooldown": False,
            }
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": keys_data}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert len(result) == 1
        key_info = result[0]
        assert isinstance(key_info, KeySpendingInfo)
        assert key_info.key_alias == "production-key-1"
        assert key_info.key_name == "Production Key 1"
        assert key_info.spend == 1234.56
        assert key_info.max_budget == 5000.0
        assert key_info.budget_duration == "30d"
        assert key_info.models == ["gpt-4", "gpt-3.5-turbo"]
        assert key_info.team_id == "team-123"
        assert key_info.user_id == "user-456"
        assert key_info.metadata == {"environment": "production", "department": "engineering"}
        assert key_info.tpm_limit == 10000
        assert key_info.rpm_limit == 100
        assert key_info.max_parallel_requests == 5
        assert key_info.blocked is False

    @patch("httpx.Client")
    def test_fetch_keys_page_with_minimal_key_data(self, mock_client):
        """Test _fetch_keys_page with minimal KeySpendingInfo data (only required fields)"""
        # Arrange
        keys_data = [
            {
                "spend": 50.0  # Only spend field, all others should default
            }
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": keys_data}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        result = self.service._fetch_keys_page()

        # Assert
        assert len(result) == 1
        key_info = result[0]
        assert isinstance(key_info, KeySpendingInfo)
        assert key_info.spend == 50.0
        assert key_info.key_alias is None
        assert key_info.key_name is None
        assert key_info.max_budget is None

    @patch("httpx.Client")
    def test_fetch_keys_page_url_construction(self, mock_client):
        """Test _fetch_keys_page constructs correct URL"""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": []}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        self.service._fetch_keys_page()

        # Assert
        expected_url = f"{config.LITE_LLM_URL}/key/list"
        call_args = mock_client.return_value.__enter__.return_value.get.call_args
        assert call_args[0][0] == expected_url

    @patch("httpx.Client")
    def test_fetch_keys_page_uses_correct_headers(self, mock_client):
        """Test _fetch_keys_page uses correct authorization headers"""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": []}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        self.service._fetch_keys_page()

        # Assert
        call_args = mock_client.return_value.__enter__.return_value.get.call_args
        headers = call_args[1]["headers"]
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == f"Bearer {config.LITE_LLM_MASTER_KEY}"

    @patch("httpx.Client")
    def test_fetch_keys_page_uses_correct_timeout(self, mock_client):
        """Test _fetch_keys_page uses correct timeout configuration"""
        # Arrange
        mock_response = MagicMock()
        mock_response.json.return_value = {"keys": []}
        mock_client.return_value.__enter__.return_value.get.return_value = mock_response

        # Act
        self.service._fetch_keys_page()

        # Assert
        mock_client.assert_called_once_with(timeout=DEFAULT_REQUEST_TIMEOUT)
