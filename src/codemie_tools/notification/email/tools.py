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

import logging
import smtplib
import socket
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, List, Tuple, Type, Optional

from langchain_core.tools import ToolException
from msal import ConfidentialClientApplication
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_tool_mixin import FileToolMixin
from codemie_tools.notification.email.models import EmailToolConfig, EmailAuthType
from codemie_tools.notification.email.tools_vars import EMAIL_TOOL

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024  # 25MB


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
    files: Optional[List[str]] = Field(
        default=None,
        description="A list of filenames to attach to the email from the uploaded input_files (e.g., ['report.pdf', 'data.xlsx']). If not specified, all uploaded files will be attached.",
    )
    timeout: Optional[float] = Field(
        default=30.0,
        description="Timeout in seconds for the SMTP operations (connection, sending). Default is 30 seconds.",
    )


class EmailTool(CodeMieTool, FileToolMixin):
    config: EmailToolConfig
    name: str = EMAIL_TOOL.name
    description: str = "Use this tool when you need to send an email notification via SMTP. Supports TO, CC, BCC, custom FROM address, and file attachments."
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

    def _validate_files(self, files: Dict[str, Tuple[bytes, str]]) -> Dict[str, Tuple[bytes, str]]:
        validated = {}
        for filename, (content, mime_type) in files.items():
            file_size = len(content)
            if file_size > MAX_ATTACHMENT_SIZE:
                size_mb = file_size / (1024 * 1024)
                max_size_mb = MAX_ATTACHMENT_SIZE / (1024 * 1024)
                raise ToolException(
                    f"File '{filename}' ({size_mb:.2f}MB) exceeds maximum attachment size of {max_size_mb:.0f}MB"
                )
            validated[filename] = (content, mime_type)

        total_size = sum(len(content) for content, _ in validated.values())
        if total_size > MAX_ATTACHMENT_SIZE:
            raise ToolException(
                f"Total attachment size {total_size / 1024**2:.1f}MB exceeds "
                f"{MAX_ATTACHMENT_SIZE / 1024**2:.0f}MB limit."
            )

        return validated

    def _get_attachments(self, files: Optional[List[str]]) -> Dict[str, Tuple[bytes, str]]:
        all_files = self._resolve_files()
        if not all_files:
            if files:
                raise ToolException(
                    f"Requested files {files} not found: no input_files configured."
                )
            return {}

        params_dict = {"files": files} if files else {}
        selected = self._filter_requested_files(all_files, params_dict)
        return self._validate_files(selected)

    def execute(
        self,
        recipient_emails: List[str],
        subject: str,
        body: str,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None,
        from_email: Optional[str] = None,
        files: Optional[List[str]] = None,
        timeout: Optional[float] = 30.0,
    ) -> str:
        try:
            host, port = self.config.url.split(":")
        except Exception:
            raise ValueError("SMTP URL must be in format 'host:port' (e.g., 'smtp.gmail.com:587').")

        from_email = self._determine_from_email(from_email)

        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = ", ".join(recipient_emails)
            if cc_emails:
                msg["Cc"] = ", ".join(cc_emails)

            body_container = MIMEMultipart("alternative")
            body_container.attach(MIMEText(body, "html"))
            msg.attach(body_container)

            files_content = self._get_attachments(files)
            for filename, (content, mime_type) in files_content.items():
                main_type, sub_type = mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream")
                attachment = MIMEBase(main_type, sub_type)
                attachment.set_payload(content)
                encoders.encode_base64(attachment)
                attachment.add_header("Content-Disposition", "attachment", filename=filename)
                msg.attach(attachment)

            with smtplib.SMTP(host, int(port), timeout=timeout) as server:
                server.starttls()
                server.ehlo()

                self._authenticate_smtp_server(server, from_email)

                all_recipients_emails = (
                    recipient_emails + (cc_emails if cc_emails else []) + (bcc_emails if bcc_emails else [])
                )
                sender = from_email if from_email else self.config.smtp_username
                server.sendmail(sender, all_recipients_emails, msg.as_string())
                server.quit()

            visible_recipients = recipient_emails + (cc_emails if cc_emails else [])
            bcc_count = len(bcc_emails) if bcc_emails else 0
            bcc_suffix = 's' if bcc_count != 1 else ''
            bcc_message = f" and {bcc_count} BCC recipient{bcc_suffix}" if bcc_count > 0 else ""
            return f"Email sent successfully to {', '.join(visible_recipients)}{bcc_message}"
        except smtplib.SMTPServerDisconnected as e:
            return f"Failed to send email due to server disconnection (possibly timeout): {e}"
        except socket.timeout as e:
            return f"Failed to send email due to timeout ({timeout}s): {e}"
        except ToolException:
            raise
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
