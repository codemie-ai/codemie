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

import smtplib
import unittest
from unittest.mock import patch


from codemie_tools.notification.email.models import EmailToolConfig, EmailAuthType
from codemie_tools.notification.email.tools import EmailTool


class TestEmailTool(unittest.TestCase):
    @patch("smtplib.SMTP")
    def test_integration_healthcheck_success(self, mock_smtp):
        # Arrange
        config = EmailToolConfig(
            url="smtp.testserver.com:587",
            smtp_username="testuser@testserver.com",
            smtp_password="password",
        )
        email_tool = EmailTool(config=config)

        # Act
        result = email_tool.healthcheck()

        # Assert
        self.assertTrue(result[0])
        self.assertEqual(result[1], "")

    @patch("smtplib.SMTP")
    def test_integration_healthcheck_smtp_response_exception(self, mock_smtp):
        # Arrange
        mock_smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPResponseException(
            451, b"Requested action aborted: local error in processing"
        )
        config = EmailToolConfig(
            url="smtp.testserver.com:587",
            smtp_username="testuser@testserver.com",
            smtp_password="password",
        )
        email_tool = EmailTool(config=config)

        # Act
        result = email_tool.healthcheck()

        # Assert
        self.assertFalse(result[0])
        self.assertIn("SMTP Code: 451", result[1])

    @patch("smtplib.SMTP")
    def test_integration_healthcheck_smtp_exception(self, mock_smtp):
        # Arrange
        mock_smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPException("Authentication failed")
        config = EmailToolConfig(
            url="smtp.testserver.com:587",
            smtp_username="testuser@testserver.com",
            smtp_password="password",
        )
        email_tool = EmailTool(config=config)

        # Act
        result = email_tool.healthcheck()

        # Assert
        self.assertFalse(result[0])
        self.assertIn("Authentication failed", result[1])

    @patch("smtplib.SMTP")
    def test_execute_success_with_cc(self, mock_smtp):
        config = EmailToolConfig(url="smtp.testserver.com:587", smtp_username="test@test.com", smtp_password="password")

        email_tool = EmailTool(config=config)

        mock_server = mock_smtp.return_value.__enter__.return_value

        result = email_tool.execute(
            recipient_emails=["user@example.com"],
            subject="Test Subject",
            body="<p>Hello</p>",
            cc_emails=["cc@example.com"],
        )

        self.assertIn("Email sent successfully", result)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@test.com", "password")
        mock_server.sendmail.assert_called_once()

    @patch("smtplib.SMTP")
    def test_execute_success_without_cc(self, mock_smtp):
        config = EmailToolConfig(url="smtp.testserver.com:587", smtp_username="test@test.com", smtp_password="password")

        email_tool = EmailTool(config=config)

        mock_server = mock_smtp.return_value.__enter__.return_value

        result = email_tool.execute(recipient_emails=["user@example.com"], subject="Test Subject", body="<p>Hello</p>")

        self.assertIn("Email sent successfully", result)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@test.com", "password")
        mock_server.sendmail.assert_called_once()

    @patch("smtplib.SMTP", side_effect=Exception("SMTP connection failed"))
    def test_execute_failure(self, mock_smtp):
        config = EmailToolConfig(url="smtp.testserver.com:587", smtp_username="test@test.com", smtp_password="password")

        email_tool = EmailTool(config=config)

        result = email_tool.execute(recipient_emails=["user@example.com"], subject="Test Subject", body="<p>Hello</p>")

        self.assertIn("Failed to send email: SMTP connection failed", result)

    @patch("smtplib.SMTP")
    def test_execute_with_bcc(self, mock_smtp):
        config = EmailToolConfig(url="smtp.testserver.com:587", smtp_username="test@test.com", smtp_password="password")

        email_tool = EmailTool(config=config)

        mock_server = mock_smtp.return_value.__enter__.return_value

        result = email_tool.execute(
            recipient_emails=["user@example.com"],
            subject="Test Subject",
            body="<p>Hello</p>",
            bcc_emails=["bcc@example.com", "bcc2@example.com"],
        )

        self.assertIn("Email sent successfully", result)
        # BCC count should be mentioned in the success message
        self.assertIn("2 BCC recipients", result)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@test.com", "password")
        mock_server.sendmail.assert_called_once()
        # Check that all recipient addresses are included in the sendmail call
        call_args = mock_server.sendmail.call_args
        self.assertIn("bcc@example.com", call_args[0][1])  # Check in recipients list
        self.assertIn("bcc2@example.com", call_args[0][1])  # Check in recipients list
        # Verify that BCC is not in the headers
        self.assertNotIn("Bcc:", call_args[0][2])

    @patch("smtplib.SMTP")
    def test_execute_with_custom_from(self, mock_smtp):
        config = EmailToolConfig(url="smtp.testserver.com:587", smtp_username="test@test.com", smtp_password="password")

        email_tool = EmailTool(config=config)

        mock_server = mock_smtp.return_value.__enter__.return_value

        result = email_tool.execute(
            recipient_emails=["user@example.com"],
            subject="Test Subject",
            body="<p>Hello</p>",
            from_email="custom@example.com",
        )

        self.assertIn("Email sent successfully", result)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@test.com", "password")
        mock_server.sendmail.assert_called_once()
        # Check that the custom From is used in sendmail
        call_args = mock_server.sendmail.call_args
        self.assertEqual("custom@example.com", call_args[0][0])
        # Check that the From header is set correctly
        self.assertIn("From: custom@example.com", call_args[0][2])

    @patch("smtplib.SMTP")
    def test_execute_with_bcc_and_custom_from(self, mock_smtp):
        config = EmailToolConfig(url="smtp.testserver.com:587", smtp_username="test@test.com", smtp_password="password")

        email_tool = EmailTool(config=config)

        mock_server = mock_smtp.return_value.__enter__.return_value

        result = email_tool.execute(
            recipient_emails=["user@example.com"],
            subject="Test Subject",
            body="<p>Hello</p>",
            cc_emails=["cc@example.com"],
            bcc_emails=["bcc@example.com"],
            from_email="custom@example.com",
        )

        self.assertIn("Email sent successfully", result)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("test@test.com", "password")
        mock_server.sendmail.assert_called_once()

        # Check that the custom From is used and all recipients are included
        call_args = mock_server.sendmail.call_args
        self.assertEqual("custom@example.com", call_args[0][0])
        self.assertIn("user@example.com", call_args[0][1])  # To recipient
        self.assertIn("cc@example.com", call_args[0][1])  # CC recipient
        self.assertIn("bcc@example.com", call_args[0][1])  # BCC recipient

        # Check headers are set correctly
        self.assertIn("From: custom@example.com", call_args[0][2])
        self.assertIn("To: user@example.com", call_args[0][2])
        self.assertIn("Cc: cc@example.com", call_args[0][2])
        self.assertNotIn("Bcc:", call_args[0][2])  # BCC should not appear in headers

    def test_config_creation_without_validation(self):
        # Test 1: Empty config should be created without errors (no validation at model level)
        config_empty = EmailToolConfig(url="smtp.testserver.com:587")
        self.assertIsNone(config_empty.smtp_username)
        self.assertIsNone(config_empty.smtp_password)
        self.assertEqual(config_empty.auth_type, EmailAuthType.BASIC)  # default auth type

        # Test 2: Config with BASIC auth
        config_basic = EmailToolConfig(
            url="smtp.testserver.com:587", smtp_username="user@example.com", smtp_password="pass"
        )
        self.assertEqual(config_basic.smtp_username, "user@example.com")
        self.assertEqual(config_basic.smtp_password, "pass")
        self.assertEqual(config_basic.auth_type, EmailAuthType.BASIC)

        # Test 3: Config with OAuth Azure
        config_oauth = EmailToolConfig(
            url="smtp.testserver.com:587",
            auth_type=EmailAuthType.OAUTH_AZURE,
            oauth_client_id="id",
            oauth_client_secret="secret",
            oauth_tenant_id="tenant",
            oauth_from_email="sender@example.com",
        )
        self.assertEqual(config_oauth.auth_type, EmailAuthType.OAUTH_AZURE)
        self.assertEqual(config_oauth.oauth_client_id, "id")
        self.assertEqual(config_oauth.oauth_from_email, "sender@example.com")
