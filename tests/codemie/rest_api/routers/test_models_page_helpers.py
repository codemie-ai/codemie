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
Page Object Model and E2E helpers for Project Settings → Models page.

Provides:
- ProjectSettingsModelsPage: Page object encapsulating Models tab interactions
- Assertion helpers for model state and persistence
- Mock API interceptors for testing error scenarios
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum
import asyncio


class ModelSaveStatus(str, Enum):
    """Result of a model save operation."""

    SUCCESS = "success"
    ERROR = "error"
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"


@dataclass
class SaveResponse:
    """Response from a model configuration save operation."""

    status: ModelSaveStatus
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    saved_model: Optional[str] = None
    previous_model: Optional[str] = None


@dataclass
class ModelConfiguration:
    """Project model configuration state."""

    project_name: str
    default_model: Optional[str]
    allowed_models: List[str]
    last_updated: Optional[str] = None


# ============================================================================
# Page Object Model: ProjectSettingsModelsPage
# ============================================================================


class ProjectSettingsModelsPage:
    """
    Page Object Model for Project Settings → Models page.

    Encapsulates all interactions with the Models tab, including:
    - Fetching current configuration
    - Selecting models
    - Saving configuration
    - Handling errors and notifications
    """

    def __init__(self, api_client, project_name: str, auth_token: str):
        """
        Initialize the Models page object.

        Args:
            api_client: HTTP client for API calls (httpx.AsyncClient or similar)
            project_name: Name of the project
            auth_token: Authentication token for API calls
        """
        self.api_client = api_client
        self.project_name = project_name
        self.auth_token = auth_token
        self.base_url = f"/v1/projects/{project_name}"
        self.headers = {"Authorization": f"Bearer {auth_token}"}

        # Local state (simulates browser localStorage)
        self._local_state: ModelConfiguration | None = None
        self._last_error: str | None = None
        self._toast_messages: List[str] = []

    # ========================================================================
    # Navigation and Setup
    # ========================================================================

    async def navigate_to_models_tab(self) -> bool:
        """
        Simulate navigating to Project Settings → Models tab.

        In a Playwright test, this would:
        1. Go to /projects/{projectName}/settings
        2. Click the "Models" tab

        Returns:
            True if navigation succeeds
        """
        # Fetch the project to ensure it exists
        try:
            await self.load_current_configuration()
            return True
        except Exception as e:
            self._last_error = str(e)
            return False

    async def load_current_configuration(self) -> ModelConfiguration:
        """
        Fetch the current model configuration from the backend.

        Calls: GET /v1/projects/{projectName}

        Returns:
            Current ModelConfiguration state

        Raises:
            Exception: If API call fails
        """
        response = await self.api_client.get(self.base_url, headers=self.headers)
        response.raise_for_status()

        data = response.json()
        self._local_state = ModelConfiguration(
            project_name=self.project_name,
            default_model=data.get("default_model"),
            allowed_models=data.get("allowed_models", []),
            last_updated=data.get("updated_at"),
        )
        return self._local_state

    # ========================================================================
    # Model Selection
    # ========================================================================

    async def get_available_models_in_dropdown(self) -> List[str]:
        """
        Get the list of models displayed in the model selector dropdown.

        Calls: GET /v1/llm_models?project_id={projectName}

        Returns:
            List of available model base names
        """
        response = await self.api_client.get(
            "/v1/llm_models",
            params={"project_id": self.project_name},
            headers=self.headers,
        )
        response.raise_for_status()

        data = response.json()
        return [model["base_name"] for model in data]

    async def select_default_model(self, model_name: str) -> None:
        """
        Select a model as the default (simulates dropdown selection + UI state change).

        Args:
            model_name: Base name of the model to select

        Raises:
            ValueError: If model not in allowed list
        """
        if self._local_state is None:
            await self.load_current_configuration()

        if model_name not in self._local_state.allowed_models:
            raise ValueError(f"Model {model_name} not in allowed models: {self._local_state.allowed_models}")

        # Update local state (simulates UI state before save)
        self._local_state.default_model = model_name

    async def clear_default_model(self) -> None:
        """Clear the default model selection."""
        if self._local_state is None:
            await self.load_current_configuration()

        self._local_state.default_model = None

    # ========================================================================
    # Save and Persistence
    # ========================================================================

    async def click_save_button(self) -> SaveResponse:
        """
        Simulate clicking the Save button and handle the response.

        Calls: PATCH /v1/projects/{projectName}/model-config (or similar)

        Returns:
            SaveResponse with status and any messages
        """
        if self._local_state is None:
            await self.load_current_configuration()

        try:
            # Call the save API
            response = await self.api_client.patch(
                f"{self.base_url}/model-config",
                json={"default_model": self._local_state.default_model},
                headers=self.headers,
            )

            # Handle different response codes
            if response.status_code == 200:
                saved_data = response.json()
                self._add_toast("Model configuration saved successfully!")
                return SaveResponse(
                    status=ModelSaveStatus.SUCCESS,
                    message="Saved successfully",
                    data=saved_data,
                    saved_model=self._local_state.default_model,
                )

            elif response.status_code == 422:
                error_detail = response.json().get("detail", "Validation error")
                self._add_toast(f"Invalid configuration: {error_detail}")
                return SaveResponse(
                    status=ModelSaveStatus.VALIDATION_ERROR,
                    message=error_detail,
                )

            elif response.status_code == 403:
                self._add_toast("You don't have permission to modify this project")
                return SaveResponse(
                    status=ModelSaveStatus.UNAUTHORIZED,
                    message="Forbidden",
                )

            elif response.status_code == 404:
                self._add_toast("Project not found")
                return SaveResponse(
                    status=ModelSaveStatus.NOT_FOUND,
                    message="Project not found",
                )

            else:
                error_msg = response.text or "Unknown error"
                self._add_toast(f"Error: {error_msg}")
                return SaveResponse(
                    status=ModelSaveStatus.ERROR,
                    message=error_msg,
                )

        except Exception as e:
            error_msg = str(e)
            self._last_error = error_msg
            self._add_toast(f"Error: {error_msg}")
            return SaveResponse(
                status=ModelSaveStatus.ERROR,
                message=error_msg,
            )

    async def reload_configuration_from_backend(self) -> ModelConfiguration:
        """
        Simulate page reload by fetching configuration fresh from backend.

        Verifies that saved changes persisted.

        Returns:
            Fresh ModelConfiguration from backend
        """
        # Clear local state to force API call
        self._local_state = None
        return await self.load_current_configuration()

    # ========================================================================
    # UI State Queries
    # ========================================================================

    def get_current_local_state(self) -> ModelConfiguration | None:
        """Get the current UI state (before save)."""
        return self._local_state

    def get_selected_default_model(self) -> str | None:
        """Get the currently selected default model in the UI."""
        return self._local_state.default_model if self._local_state else None

    def get_toast_messages(self) -> List[str]:
        """Get all toast/notification messages displayed."""
        return self._toast_messages.copy()

    def has_success_toast(self) -> bool:
        """Check if a success notification was displayed."""
        return any("success" in msg.lower() or "saved" in msg.lower() for msg in self._toast_messages)

    def has_error_toast(self) -> bool:
        """Check if an error notification was displayed."""
        return any("error" in msg.lower() or "failed" in msg.lower() for msg in self._toast_messages)

    def get_last_error(self) -> str | None:
        """Get the last error message."""
        return self._last_error

    def clear_toasts(self) -> None:
        """Clear toast message history."""
        self._toast_messages.clear()

    # ========================================================================
    # Helpers
    # ========================================================================

    def _add_toast(self, message: str) -> None:
        """Add a toast message to the queue."""
        self._toast_messages.append(message)


# ============================================================================
# Assertion Helpers
# ============================================================================


class ModelConfigurationAssertions:
    """Helper class for asserting model configuration state."""

    @staticmethod
    async def assert_model_persisted(
        page: ProjectSettingsModelsPage,
        expected_model: str,
        message: str = "Model did not persist after save",
    ) -> None:
        """
        Assert that a model was saved and persisted.

        Steps:
        1. Reload from backend
        2. Verify the model matches expected

        Args:
            page: ProjectSettingsModelsPage instance
            expected_model: Expected default model name
            message: Assertion error message

        Raises:
            AssertionError: If model did not persist
        """
        config = await page.reload_configuration_from_backend()
        assert config.default_model == expected_model, message

    @staticmethod
    async def assert_model_not_persisted(
        page: ProjectSettingsModelsPage,
        unexpected_model: str,
        message: str = "Model should not have been saved",
    ) -> None:
        """
        Assert that a model was NOT saved (e.g., after an error).

        Args:
            page: ProjectSettingsModelsPage instance
            unexpected_model: Model that should not be set
            message: Assertion error message

        Raises:
            AssertionError: If model was persisted
        """
        config = await page.reload_configuration_from_backend()
        assert config.default_model != unexpected_model, message

    @staticmethod
    def assert_save_succeeded(response: SaveResponse, message: str = "Save should have succeeded") -> None:
        """
        Assert that a save operation succeeded.

        Args:
            response: SaveResponse from click_save_button()
            message: Assertion error message

        Raises:
            AssertionError: If save did not succeed
        """
        assert response.status == ModelSaveStatus.SUCCESS, message

    @staticmethod
    def assert_save_failed_with_validation_error(
        response: SaveResponse, message: str = "Should have failed with validation error"
    ) -> None:
        """
        Assert that a save operation failed with a validation error.

        Args:
            response: SaveResponse from click_save_button()
            message: Assertion error message

        Raises:
            AssertionError: If error status is incorrect
        """
        assert response.status == ModelSaveStatus.VALIDATION_ERROR, f"{message}. Got: {response.status}"

    @staticmethod
    def assert_unauthorized(response: SaveResponse, message: str = "Should have failed with unauthorized") -> None:
        """
        Assert that a save operation was rejected as unauthorized.

        Args:
            response: SaveResponse from click_save_button()
            message: Assertion error message

        Raises:
            AssertionError: If error status is incorrect
        """
        assert response.status == ModelSaveStatus.UNAUTHORIZED, f"{message}. Got: {response.status}"


# ============================================================================
# Mock API Interceptors (for error injection testing)
# ============================================================================


class MockApiInterceptor:
    """Mock API client that can simulate errors and delays."""

    def __init__(self, real_client):
        """Initialize with a real HTTP client."""
        self.real_client = real_client
        self.error_on_next_request: Optional[Exception] = None
        self.delay_ms: int = 0
        self.response_override: Optional[Any] = None

    async def get(self, url: str, **kwargs):
        """Intercepted GET request."""
        await self._apply_delay()
        if self.error_on_next_request:
            exc = self.error_on_next_request
            self.error_on_next_request = None
            raise exc
        return await self.real_client.get(url, **kwargs)

    async def patch(self, url: str, **kwargs):
        """Intercepted PATCH request."""
        await self._apply_delay()
        if self.error_on_next_request:
            exc = self.error_on_next_request
            self.error_on_next_request = None
            raise exc
        return await self.real_client.patch(url, **kwargs)

    async def _apply_delay(self) -> None:
        """Apply a simulated network delay."""
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000.0)

    def inject_error_on_next_request(self, exc: Exception) -> None:
        """
        Inject an error to be raised on the next request.

        Useful for testing error handling.
        """
        self.error_on_next_request = exc

    def set_delay_ms(self, delay_ms: int) -> None:
        """Set a simulated network delay in milliseconds."""
        self.delay_ms = delay_ms
