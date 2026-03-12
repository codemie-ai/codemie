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

from email.message import EmailMessage

import aiosmtplib

from codemie.configs import config
from codemie.configs.logger import logger


class EmailService:
    """Email service for sending verification and password reset emails"""

    async def send_email(self, to: str, subject: str, html_body: str) -> bool:
        """Send email via SMTP with TLS

        Args:
            to: Recipient email address
            subject: Email subject
            html_body: HTML email body

        Returns:
            True if email sent successfully

        Raises:
            Exception: On SMTP failure (caller should handle)
        """
        message = EmailMessage()
        message["From"] = f"{config.EMAIL_FROM_NAME} <{config.EMAIL_FROM_ADDRESS}>"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(html_body, subtype="html")

        try:
            await aiosmtplib.send(
                message,
                hostname=config.EMAIL_SMTP_HOST,
                port=config.EMAIL_SMTP_PORT,
                username=config.EMAIL_SMTP_USERNAME,
                password=config.EMAIL_SMTP_PASSWORD,
                start_tls=config.EMAIL_USE_TLS,  # STARTTLS for port 587
                timeout=10,  # 10 second timeout
            )
            logger.info("Email sent successfully to recipient")  # Don't log email address
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            raise  # Let caller handle failure

    async def send_verification_email(self, email: str, token: str) -> bool:
        """Send email verification link

        Args:
            email: Recipient email address
            token: Verification token (raw, not hashed)

        Returns:
            True if sent successfully

        Raises:
            Exception: On SMTP failure (fail-closed for registration)
        """
        verification_url = f"{config.FRONTEND_URL}/verify-email?token={token}"

        subject = "Verify your CodeMie account"
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 20px; background-color: #f9f9f9; }}
        .button {{
            display: inline-block;
            background-color: #4F46E5;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .footer {{ font-size: 12px; color: #666; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to CodeMie!</h1>
        </div>
        <div class="content">
            <p>Thank you for registering. Please verify your email address by clicking the button below:</p>

            <p style="text-align: center;">
                <a href="{verification_url}" class="button">Verify Email Address</a>
            </p>

            <p>Or copy and paste this URL into your browser:</p>
            <p style="word-break: break-all; background: #eee; padding: 10px; font-size: 12px;">
                {verification_url}
            </p>

            <p><strong>This link will expire in 24 hours.</strong></p>

            <p class="footer">
                If you didn't create this account, please ignore this email.
            </p>
        </div>
    </div>
</body>
</html>
"""

        return await self.send_email(email, subject, html_body)

    async def send_password_reset_email(self, email: str, token: str) -> None:
        """Send password reset link (fail-safe for privacy)

        Args:
            email: Recipient email address
            token: Reset token (raw, not hashed)

        Note:
            Never raises exceptions to prevent email enumeration.
            Failures are caught and logged silently.
        """
        try:
            reset_url = f"{config.FRONTEND_URL}/reset-password?token={token}"

            subject = "Reset your CodeMie password"
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 20px; background-color: #f9f9f9; }}
        .button {{
            display: inline-block;
            background-color: #4F46E5;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .warning {{ background-color: #FEF3C7; padding: 10px; border-left: 4px solid #F59E0B; margin: 20px 0; }}
        .footer {{ font-size: 12px; color: #666; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Password Reset Request</h1>
        </div>
        <div class="content">
            <p>You requested to reset your CodeMie password. Click the button below to create a new password:</p>

            <p style="text-align: center;">
                <a href="{reset_url}" class="button">Reset Password</a>
            </p>

            <p>Or copy and paste this URL into your browser:</p>
            <p style="word-break: break-all; background: #eee; padding: 10px; font-size: 12px;">
                {reset_url}
            </p>

            <div class="warning">
                <strong>Security Notice:</strong> This link will expire in 24 hours.
            </div>

            <p class="footer">
                If you didn't request this password reset, please ignore this email.
                Your password will remain unchanged.
            </p>
        </div>
    </div>
</body>
</html>
"""

            await self.send_email(email, subject, html_body)
        except Exception as e:
            # Fail-safe: don't reveal whether email exists
            logger.warning(f"Failed to send password reset email: {e}", exc_info=True)

    def is_configured(self) -> bool:
        """Check if email service is configured

        Returns:
            True if SMTP settings are configured
        """
        return bool(config.EMAIL_SMTP_HOST and config.EMAIL_FROM_ADDRESS)


# Singleton instance
email_service = EmailService()
