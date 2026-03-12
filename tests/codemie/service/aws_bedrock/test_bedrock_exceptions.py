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

import pytest
from unittest.mock import patch
from botocore.exceptions import ClientError, BotoCoreError
from fastapi import status

from codemie.service.aws_bedrock.exceptions import (
    SettingNotFoundException,
    SettingAWSCredentialTypeRequired,
    SettingAccessDeniedException,
    SettingIdRequiredException,
    AwsCredentialsNotFoundException,
    _handle_custom_exceptions,
    _handle_client_error,
    aws_service_exception_handler,
)
from codemie.core.exceptions import ExtendedHTTPException


# Tests for custom exception classes
class TestCustomExceptions:
    def test_setting_not_found_exception(self):
        """Test SettingNotFoundException initialization."""
        exc = SettingNotFoundException("setting-123")
        assert exc.setting_id == "setting-123"

    def test_setting_aws_credential_type_required(self):
        """Test SettingAWSCredentialTypeRequired initialization."""
        exc = SettingAWSCredentialTypeRequired("setting-123")
        assert exc.setting_id == "setting-123"

    def test_setting_access_denied_exception(self):
        """Test SettingAccessDeniedException initialization."""
        exc = SettingAccessDeniedException("user-123", "setting-456", "project1")
        assert exc.user_id == "user-123"
        assert exc.setting_id == "setting-456"
        assert exc.project_name == "project1"

    def test_setting_id_required_exception(self):
        """Test SettingIdRequiredException initialization."""
        exc = SettingIdRequiredException()
        assert isinstance(exc, Exception)

    def test_aws_credentials_not_found_exception(self):
        """Test AwsCredentialsNotFoundException initialization."""
        exc = AwsCredentialsNotFoundException("setting-123")
        assert exc.setting_id == "setting-123"


# Tests for _handle_custom_exceptions
class TestHandleCustomExceptions:
    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_setting_not_found_exception(self, mock_logger):
        """Test handling of SettingNotFoundException."""
        exc = SettingNotFoundException("setting-123")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_custom_exceptions(exc)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        assert exc_info.value.message == "Setting not found"
        assert "setting-123" in exc_info.value.details
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_setting_aws_credential_type_required(self, mock_logger):
        """Test handling of SettingAWSCredentialTypeRequired."""
        exc = SettingAWSCredentialTypeRequired("setting-123")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_custom_exceptions(exc)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        assert exc_info.value.message == "Setting is not of type AWS"
        assert "setting-123" in exc_info.value.details
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_setting_access_denied_exception(self, mock_logger):
        """Test handling of SettingAccessDeniedException."""
        exc = SettingAccessDeniedException("user-123", "setting-456", "project1")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_custom_exceptions(exc)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.message == "Access denied"
        assert "user-123" in exc_info.value.details
        assert "setting-456" in exc_info.value.details
        assert "project1" in exc_info.value.details
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_setting_id_required_exception(self, mock_logger):
        """Test handling of SettingIdRequiredException."""
        exc = SettingIdRequiredException()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_custom_exceptions(exc)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.message == "Setting ID is required"
        assert "Setting ID is required" in exc_info.value.details
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_aws_credentials_not_found_exception(self, mock_logger):
        """Test handling of AwsCredentialsNotFoundException."""
        exc = AwsCredentialsNotFoundException("setting-123")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_custom_exceptions(exc)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        assert exc_info.value.message == "AWS credentials not found"
        assert "setting-123" in exc_info.value.details
        mock_logger.error.assert_called_once()


# Tests for _handle_client_error
class TestHandleClientError:
    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_client_error_resource_not_found(self, mock_logger):
        """Test handling of ResourceNotFoundException."""
        client_error = ClientError(
            error_response={"Error": {"Code": "ResourceNotFoundException", "Message": "Resource not found"}},
            operation_name="TestOperation",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_client_error(client_error, "test entity", "setting-123")

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        assert "AWS Resource not found" in exc_info.value.message
        assert "test entity" in exc_info.value.details
        assert "setting-123" in exc_info.value.details
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_client_error_access_denied(self, mock_logger):
        """Test handling of AccessDeniedException."""
        client_error = ClientError(
            error_response={"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            operation_name="TestOperation",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_client_error(client_error, "test entity", "setting-123")

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN
        assert "AWS Access Denied" in exc_info.value.message
        assert "test entity" in exc_info.value.details
        assert "setting-123" in exc_info.value.details
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_client_error_generic(self, mock_logger):
        """Test handling of generic ClientError."""
        client_error = ClientError(
            error_response={"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
            operation_name="TestOperation",
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_client_error(client_error, "test entity", "setting-123")

        assert exc_info.value.code == status.HTTP_502_BAD_GATEWAY
        assert "AWS Client Error: ThrottlingException" in exc_info.value.message
        assert "test entity" in exc_info.value.details
        assert "setting-123" in exc_info.value.details
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_handle_client_error_no_error_details(self, mock_logger):
        """Test handling of ClientError without error details."""
        client_error = ClientError(error_response={}, operation_name="TestOperation")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            _handle_client_error(client_error, "test entity", "setting-123")

        assert exc_info.value.code == status.HTTP_502_BAD_GATEWAY
        assert "AWS Client Error: AWSClientError" in exc_info.value.message
        mock_logger.error.assert_called_once()


# Tests for aws_service_exception_handler decorator
class TestAwsServiceExceptionHandler:
    def test_decorator_success(self):
        """Test decorator with successful function execution."""

        @aws_service_exception_handler("test entity")
        def test_function(setting_id=None):
            return "success"

        result = test_function(setting_id="setting-123")
        assert result == "success"

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_decorator_setting_not_found_exception(self, mock_logger):
        """Test decorator handling SettingNotFoundException."""

        @aws_service_exception_handler("test entity")
        def test_function(setting_id=None):
            raise SettingNotFoundException("setting-123")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            test_function(setting_id="setting-123")

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_decorator_client_error(self, mock_logger):
        """Test decorator handling ClientError."""

        @aws_service_exception_handler("test entity")
        def test_function(setting_id=None):
            raise ClientError(
                error_response={"Error": {"Code": "ResourceNotFoundException", "Message": "Resource not found"}},
                operation_name="TestOperation",
            )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            test_function(setting_id="setting-123")

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_decorator_botocore_error(self, mock_logger):
        """Test decorator handling BotoCoreError."""

        @aws_service_exception_handler("test entity")
        def test_function(setting_id=None):
            raise BotoCoreError(msg="Test BotoCoreError")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            test_function(setting_id="setting-123")

        assert exc_info.value.code == status.HTTP_502_BAD_GATEWAY
        assert "AWS BotoCore Error" in exc_info.value.message
        mock_logger.error.assert_called_once()

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_decorator_generic_exception(self, mock_logger):
        """Test decorator handling generic Exception."""

        @aws_service_exception_handler("test entity")
        def test_function(setting_id=None):
            raise ValueError("Something went wrong")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            test_function(setting_id="setting-123")

        assert exc_info.value.code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Unexpected error" in exc_info.value.message
        mock_logger.error.assert_called_once()

    def test_decorator_no_setting_id_in_kwargs(self):
        """Test decorator when setting_id is not in kwargs."""

        @aws_service_exception_handler("test entity")
        def test_function():
            raise ValueError("Something went wrong")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            test_function()

        # Should use "unknown" as default setting_id
        assert exc_info.value.code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves function metadata."""

        @aws_service_exception_handler("test entity")
        def test_function():
            """Test function docstring."""
            return "test"

        assert test_function.__name__ == "test_function"
        assert test_function.__doc__ == "Test function docstring."

    @patch("codemie.service.aws_bedrock.exceptions.logger")
    def test_decorator_with_args_and_kwargs(self, mock_logger):
        """Test decorator with both args and kwargs."""

        @aws_service_exception_handler("test entity")
        def test_function(arg1, arg2, setting_id=None, other_kwarg=None):
            if setting_id == "error":
                raise SettingNotFoundException(setting_id)
            return f"{arg1}-{arg2}-{setting_id}-{other_kwarg}"

        # Test success case
        result = test_function("a", "b", setting_id="setting-123", other_kwarg="test")
        assert result == "a-b-setting-123-test"

        # Test error case
        with pytest.raises(ExtendedHTTPException):
            test_function("a", "b", setting_id="error", other_kwarg="test")
