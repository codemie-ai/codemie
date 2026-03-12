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

from __future__ import annotations

from unittest.mock import ANY, AsyncMock, patch

import pytest

from codemie.service.email_service import EmailService, email_service


class TestEmailService:
    """Test suite for EmailService - SMTP email operations"""

    @pytest.fixture
    def service(self):
        """Create EmailService instance for testing"""
        return EmailService()

    @pytest.mark.asyncio
    @patch("codemie.service.email_service.aiosmtplib.send", new_callable=AsyncMock)
    @patch("codemie.service.email_service.config")
    async def test_send_email_success(self, mock_config, mock_aiosmtplib_send, service):
        """Test that send_email successfully sends via SMTP"""
        # Arrange
        mock_config.EMAIL_FROM_NAME = "CodeMie"
        mock_config.EMAIL_FROM_ADDRESS = "noreply@codemie.com"
        mock_config.EMAIL_SMTP_HOST = "smtp.example.com"
        mock_config.EMAIL_SMTP_PORT = 587
        mock_config.EMAIL_SMTP_USERNAME = "smtp_user"
        mock_config.EMAIL_SMTP_PASSWORD = "smtp_pass"
        mock_config.EMAIL_USE_TLS = True

        to = "recipient@example.com"
        subject = "Test Subject"
        html_body = "<p>Test email body</p>"

        mock_aiosmtplib_send.return_value = None

        # Act
        result = await service.send_email(to, subject, html_body)

        # Assert
        assert result is True
        mock_aiosmtplib_send.assert_called_once()

        # Verify message structure
        call_args = mock_aiosmtplib_send.call_args
        message = call_args[0][0]
        assert message["To"] == to
        assert message["Subject"] == subject
        assert message["From"] == "CodeMie <noreply@codemie.com>"

        # Verify SMTP parameters
        assert call_args[1]["hostname"] == "smtp.example.com"
        assert call_args[1]["port"] == 587
        assert call_args[1]["username"] == "smtp_user"
        assert call_args[1]["password"] == "smtp_pass"
        assert call_args[1]["start_tls"] is True
        assert call_args[1]["timeout"] == 10

    @pytest.mark.asyncio
    @patch("codemie.service.email_service.aiosmtplib.send", new_callable=AsyncMock)
    @patch("codemie.service.email_service.config")
    async def test_send_email_smtp_failure_raises(self, mock_config, mock_aiosmtplib_send, service):
        """Test that send_email propagates SMTP exceptions"""
        # Arrange
        mock_config.EMAIL_FROM_NAME = "CodeMie"
        mock_config.EMAIL_FROM_ADDRESS = "noreply@codemie.com"
        mock_config.EMAIL_SMTP_HOST = "smtp.example.com"
        mock_config.EMAIL_SMTP_PORT = 587
        mock_config.EMAIL_SMTP_USERNAME = "smtp_user"
        mock_config.EMAIL_SMTP_PASSWORD = "smtp_pass"
        mock_config.EMAIL_USE_TLS = True

        mock_aiosmtplib_send.side_effect = Exception("SMTP connection failed")

        # Act & Assert
        with pytest.raises(Exception) as exc_info:
            await service.send_email("fail@example.com", "Subject", "<p>Body</p>")

        assert "SMTP connection failed" in str(exc_info.value)

    @pytest.mark.asyncio
    @patch.object(EmailService, "send_email", new_callable=AsyncMock)
    @patch("codemie.service.email_service.config")
    async def test_send_verification_email_builds_url(self, mock_config, mock_send_email, service):
        """Test that send_verification_email constructs correct verification URL"""
        # Arrange
        mock_config.FRONTEND_URL = "https://codemie.example.com"
        email = "verify@example.com"
        token = "verification-token-123"
        mock_send_email.return_value = True

        # Act
        result = await service.send_verification_email(email, token)

        # Assert
        assert result is True
        mock_send_email.assert_called_once()

        call_args = mock_send_email.call_args
        assert call_args[0][0] == email
        assert call_args[0][1] == "Verify your CodeMie account"

        html_body = call_args[0][2]
        expected_url = f"https://codemie.example.com/verify-email?token={token}"
        assert expected_url in html_body
        assert "Verify Email Address" in html_body

    @pytest.mark.asyncio
    @patch.object(EmailService, "send_email", new_callable=AsyncMock)
    @patch("codemie.service.email_service.config")
    async def test_send_verification_email_delegates_to_send_email(self, mock_config, mock_send_email, service):
        """Test that send_verification_email delegates to send_email"""
        # Arrange
        mock_config.FRONTEND_URL = "https://codemie.example.com"
        email = "delegate@example.com"
        token = "token-456"
        mock_send_email.return_value = True

        # Act
        result = await service.send_verification_email(email, token)

        # Assert
        assert result is True
        mock_send_email.assert_called_once_with(email, "Verify your CodeMie account", ANY)

    @pytest.mark.asyncio
    @patch.object(EmailService, "send_email", new_callable=AsyncMock)
    @patch("codemie.service.email_service.config")
    async def test_send_password_reset_email_success(self, mock_config, mock_send_email, service):
        """Test that send_password_reset_email sends reset email"""
        # Arrange
        mock_config.FRONTEND_URL = "https://codemie.example.com"
        email = "reset@example.com"
        token = "reset-token-789"
        mock_send_email.return_value = True

        # Act
        await service.send_password_reset_email(email, token)

        # Assert
        mock_send_email.assert_called_once()

        call_args = mock_send_email.call_args
        assert call_args[0][0] == email
        assert call_args[0][1] == "Reset your CodeMie password"

        html_body = call_args[0][2]
        expected_url = f"https://codemie.example.com/reset-password?token={token}"
        assert expected_url in html_body
        assert "Reset Password" in html_body
        assert "Security Notice" in html_body

    @pytest.mark.asyncio
    @patch.object(EmailService, "send_email", new_callable=AsyncMock)
    @patch("codemie.service.email_service.config")
    @patch("codemie.service.email_service.logger")
    async def test_send_password_reset_email_swallows_exception(
        self, mock_logger, mock_config, mock_send_email, service
    ):
        """Test that send_password_reset_email catches exceptions (fail-safe for privacy)"""
        # Arrange
        mock_config.FRONTEND_URL = "https://codemie.example.com"
        email = "failsafe@example.com"
        token = "token-fail"
        mock_send_email.side_effect = Exception("SMTP error")

        # Act - should not raise
        await service.send_password_reset_email(email, token)

        # Assert
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "Failed to send password reset email" in call_args

    @patch("codemie.service.email_service.config")
    def test_is_configured_true(self, mock_config, service):
        """Test that is_configured returns True when SMTP is configured"""
        # Arrange
        mock_config.EMAIL_SMTP_HOST = "smtp.example.com"
        mock_config.EMAIL_FROM_ADDRESS = "noreply@codemie.com"

        # Act
        result = service.is_configured()

        # Assert
        assert result is True

    @patch("codemie.service.email_service.config")
    def test_is_configured_false_missing_host(self, mock_config, service):
        """Test that is_configured returns False when SMTP host is missing"""
        # Arrange
        mock_config.EMAIL_SMTP_HOST = None
        mock_config.EMAIL_FROM_ADDRESS = "noreply@codemie.com"

        # Act
        result = service.is_configured()

        # Assert
        assert result is False

    @patch("codemie.service.email_service.config")
    def test_is_configured_false_missing_from(self, mock_config, service):
        """Test that is_configured returns False when FROM address is missing"""
        # Arrange
        mock_config.EMAIL_SMTP_HOST = "smtp.example.com"
        mock_config.EMAIL_FROM_ADDRESS = None

        # Act
        result = service.is_configured()

        # Assert
        assert result is False

    @patch("codemie.service.email_service.config")
    def test_is_configured_false_empty_strings(self, mock_config, service):
        """Test that is_configured returns False for empty strings"""
        # Arrange
        mock_config.EMAIL_SMTP_HOST = ""
        mock_config.EMAIL_FROM_ADDRESS = ""

        # Act
        result = service.is_configured()

        # Assert
        assert result is False


class TestEmailServiceSingleton:
    """Test the email_service singleton instance"""

    def test_singleton_instance_exists(self):
        """Test that email_service singleton is properly initialized"""
        # Assert
        assert email_service is not None
        assert isinstance(email_service, EmailService)

    @patch("codemie.service.email_service.config")
    def test_singleton_is_configured(self, mock_config):
        """Test that singleton can check configuration"""
        # Arrange
        mock_config.EMAIL_SMTP_HOST = "smtp.example.com"
        mock_config.EMAIL_FROM_ADDRESS = "noreply@codemie.com"

        # Act
        result = email_service.is_configured()

        # Assert
        assert result is True
