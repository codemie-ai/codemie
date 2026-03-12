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
import socket
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Type, Optional

from langchain_core.tools import ToolException
from msal import ConfidentialClientApplication
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.notification.email.models import EmailToolConfig, EmailAuthType
from codemie_tools.notification.email.tools_vars import EMAIL_TOOL


class EmailToolInput(BaseModel):
    recipient_emails: List[str] = Field(..., description="A list of recipient email addresses")
    subject: str = Field(..., description="The email subject")
    body: str = Field(..., description="The body of the email (can include HTML formatting)")
    cc_emails: Optional[List[str]] = Field(default=None, description="A list of cc (carbon copy) email addresses")
    bcc_emails: Optional[List[str]] = Field(
        default=None, description="A list of bcc (blind carbon copy) email addresses"
    )
    from_email: Optional[str] = Field(
        default=None,
        description="Sender email address. If not specified, the configured SMTP username will be used as the sender.",
    )
    timeout: Optional[float] = Field(
        default=30.0,
        description="Timeout in seconds for the SMTP operations (connection, sending). Default is 30 seconds.",
    )


class EmailTool(CodeMieTool):
    config: EmailToolConfig
    name: str = EMAIL_TOOL.name
    description: str = "Use this tool when you need to send an email notification via SMTP. Supports TO, CC, BCC, and custom FROM address."
    args_schema: Type[BaseModel] = EmailToolInput

    def _get_oauth_token_azure(self) -> str:
        """Get OAuth access token for Microsoft Entra ID / Microsoft 365."""
        # Build authority from base URL and tenant_id
        msal_authority = f"{self.config.oauth_authority}/{self.config.oauth_tenant_id}"
        msal_scope = [self.config.oauth_scope]

        msal_app = ConfidentialClientApplication(
            client_id=self.config.oauth_client_id,
            client_credential=self.config.oauth_client_secret,
            authority=msal_authority,
        )

        result = msal_app.acquire_token_silent(scopes=msal_scope, account=None)
        if not result:
            result = msal_app.acquire_token_for_client(scopes=msal_scope)

        if "access_token" in result:
            return result["access_token"]
        else:
            error_msg = result.get("error_description", result.get("error", "Unknown error"))
            raise ToolException(f"Failed to acquire OAuth access token: {error_msg}")

    def _determine_from_email(self, from_email: Optional[str]) -> str:
        """
        Determine the FROM email address based on auth type and provided value.

        Args:
            from_email: Optional sender email address provided by user

        Returns:
            Resolved FROM email address

        Raises:
            ValueError: If authentication type is not supported
        """
        if from_email:
            return from_email

        if self.config.auth_type == EmailAuthType.BASIC:
            return self.config.smtp_username
        elif self.config.auth_type == EmailAuthType.OAUTH_AZURE:
            return self.config.oauth_from_email
        else:
            raise ValueError(f"Unsupported authentication type: {self.config.auth_type}")

    def _authenticate_smtp_server(self, server: smtplib.SMTP, from_email: str) -> None:
        """
        Authenticate with SMTP server based on configured auth type.

        Args:
            server: SMTP server instance
            from_email: Email address for OAuth authentication
        """
        if self.config.auth_type == EmailAuthType.BASIC:
            # SMTP basic authentication
            server.login(self.config.smtp_username, self.config.smtp_password)

        elif self.config.auth_type == EmailAuthType.OAUTH_AZURE:
            # OAuth via Microsoft Entra ID
            access_token = self._get_oauth_token_azure()
            auth_string = f"user={from_email}\x01auth=Bearer {access_token}\x01\x01"
            auth_string_encoded = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
            server.docmd("AUTH", "XOAUTH2 " + auth_string_encoded)

    def execute(
        self,
        recipient_emails: List[str],
        subject: str,
        body: str,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None,
        from_email: Optional[str] = None,
        timeout: Optional[float] = 30.0,
    ) -> str:
        """
        Send an email via SMTP with a configurable timeout.

        Args:
            recipient_emails: List of recipient email addresses
            subject: Email subject
            body: Email body content (can include HTML)
            cc_emails: Optional list of CC email addresses
            bcc_emails: Optional list of BCC email addresses
            from_email: Optional sender email address (overrides config if provided)
            timeout: Optional timeout in seconds for SMTP operations (default: 30 seconds)

        Returns:
            Confirmation message on success
        """
        # Additional URL format validation
        try:
            host, port = self.config.url.split(":")
        except Exception:
            raise ValueError("SMTP URL must be in format 'host:port' (e.g., 'smtp.gmail.com:587').")

        # Determine FROM email based on auth type
        from_email = self._determine_from_email(from_email)

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = ", ".join(recipient_emails)
            if cc_emails:
                msg["Cc"] = ", ".join(cc_emails)
            # BCC is handled in sendmail recipients but not added as a header

            part = MIMEText(body, "html")
            msg.attach(part)

            with smtplib.SMTP(host, int(port), timeout=timeout) as server:
                server.starttls()
                server.ehlo()

                # Authenticate with SMTP server
                self._authenticate_smtp_server(server, from_email)

                # Combine all recipients for sendmail (to, cc, bcc)
                all_recipients_emails = (
                    recipient_emails + (cc_emails if cc_emails else []) + (bcc_emails if bcc_emails else [])
                )
                # Use the from_email if provided, otherwise use the configured SMTP username
                sender = from_email if from_email else self.config.smtp_username
                server.sendmail(sender, all_recipients_emails, msg.as_string())
                server.quit()

            # Don't expose BCC recipients in the success message
            visible_recipients = recipient_emails + (cc_emails if cc_emails else [])
            bcc_count = len(bcc_emails) if bcc_emails else 0
            bcc_suffix = 's' if bcc_count != 1 else ''
            bcc_message = f" and {bcc_count} BCC recipient{bcc_suffix}" if bcc_count > 0 else ""
            return f"Email sent successfully to {', '.join(visible_recipients)}{bcc_message}"
        except smtplib.SMTPServerDisconnected as e:
            return f"Failed to send email due to server disconnection (possibly timeout): {e}"
        except socket.timeout as e:
            return f"Failed to send email due to timeout ({timeout}s): {e}"
        except Exception as e:
            return f"Failed to send email: {e}"

    def _healthcheck(self):
        """
        Check if the SMTP connection can be established.

        Returns:
            Nothing if successful, raises an exception on failure that will be caught by the parent class.
        """
        try:
            host, port = self.config.url.split(":")
            # Use a default timeout of 10 seconds for healthcheck
            with smtplib.SMTP(host, int(port), timeout=10.0) as server:
                server.starttls()
                server.ehlo()

                # Determine from_email for authentication
                from_email = self._determine_from_email(None)

                # Authenticate with SMTP server
                self._authenticate_smtp_server(server, from_email)

                server.noop()
                server.quit()
        except smtplib.SMTPResponseException as e:
            # Specific handling for SMTP response exceptions
            error_message = f"SMTP Code: {e.smtp_code}, Message: {e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else e.smtp_error}"
            raise smtplib.SMTPException(error_message)
