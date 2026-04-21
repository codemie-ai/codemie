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

from enum import Enum

from fastapi import BackgroundTasks, HTTPException, Request, status

from codemie.configs import logger
from codemie.core.constants import CodeIndexType
from codemie.core.models import BaseResponse, GitRepo
from codemie.rest_api.routers.utils import run_in_thread_pool
from codemie.rest_api.security.user import User
from codemie.service.constants import FullDatasourceTypes
from codemie.service.monitoring.webhook_monitoring_service import WebhookMonitoringService
from codemie.service.provider.datasource.provider_datasource_reindex_service import ProviderDatasourceReindexService
from codemie.service.settings.settings import SettingsService
from codemie.service.workflow_service import WorkflowService
from codemie.triggers.actors.assistant import invoke_assistant
from codemie.triggers.actors.datasource import reindex_code, reindex_confluence, reindex_google, reindex_jira
from codemie.triggers.actors.workflow import invoke_workflow
from codemie.triggers.bindings.github_webhook_security import GitHubWebhookSecurity
from codemie.triggers.bindings.utils import validate_assistant, validate_datasource
from codemie.triggers.trigger_exceptions import NotImplementedDatasource
from codemie.triggers.trigger_models import (
    CodeReindexTask,
    ConfluenceReindexTask,
    GoogleReindexTask,
    JiraReindexTask,
)


class ResourceType(Enum):
    ASSISTANT = "assistant"
    WORKFLOW = "workflow"
    DATASOURCE = "datasource"


class WebhookService:
    SECURE_HEADER_NAME = "secure_header_name"
    SECURE_HEADER_VALUE = "secure_header_value"

    # GitHub webhook security (signature-based verification)
    GITHUB_WEBHOOK_SECRET = "github_webhook_secret"
    GITHUB_EVENT_FILTER = "github_event_filter"
    GITHUB_REQUIRE_SHA256 = "github_require_sha256"

    # Webhook configuration
    RESOURCE_TYPE = "resource_type"
    WEBHOOK = "webhook"
    IS_ENABLED = "is_enabled"
    RESOURCE_ID = "resource_id"
    INDEX_TYPE = "index_type"
    JQL = "jql"

    # Response messages
    WEBHOOK_INVOKED_SUCCESSFULLY = "Webhook invoked successfully"
    WEBHOOK_NOT_FOUND_OR_NOT_ENABLED = "Webhook with ID '{}' not found or not enabled"
    INVALID_SECURITY_HEADER = "Invalid security header"
    ASSISTANT_NOT_FOUND = "Assistant with id '{}' not found"
    WORKFLOW_NOT_FOUND = "Workflow with id '{}' not found"
    DATASOURCE_NOT_FOUND = "Datasource with id '{}' not found"
    UNSUPPORTED_RESOURCE_TYPE = "Unsupported resource type '{}'"

    @classmethod
    async def invoke_webhook_logic(cls, request: Request, webhook_id: str, background_tasks: BackgroundTasks):
        logger.info("Received webhook invocation request with WebhookID: '%s'", webhook_id)

        # Read request body once for signature verification and payload processing
        raw_payload = await request.body()

        query = {"credential_values.key.keyword": "webhook_id", "credential_values.value.keyword": webhook_id}
        setting = SettingsService.retrieve_setting(query)
        if not setting:
            logger.error("Webhook not found for WebhookID: '%s'", webhook_id)
            WebhookMonitoringService.send_webhook_invocation_metric(
                webhook_id=webhook_id,
                project_name="unknown",
                user_id="unknown",
                success=False,
                resource_type="unknown",
                resource_id="unknown",
                webhook_alias="unknown",
                additional_attributes={"error_cause": "webhook_not_found"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=cls.WEBHOOK_NOT_FOUND_OR_NOT_ENABLED.format(webhook_id)
            )

        resource_type = setting.credential(cls.RESOURCE_TYPE)
        resource_id = setting.credential(cls.RESOURCE_ID)
        is_enabled = setting.credential(cls.IS_ENABLED)
        if not is_enabled:
            logger.warning(
                "Webhook invocation attempt failed: webhook '%s' is disabled. "
                "Project: '%s', WebhookID: '%s', UserID: '%s'",
                setting.alias,
                setting.project_name,
                webhook_id,
                setting.user_id,
            )
            WebhookMonitoringService.send_webhook_invocation_metric(
                webhook_id=webhook_id,
                project_name=setting.project_name,
                user_id=setting.user_id,
                success=False,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_alias=setting.alias,
                additional_attributes={"error_cause": "webhook_disabled"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=cls.WEBHOOK_NOT_FOUND_OR_NOT_ENABLED.format(webhook_id)
            )

        # Verify webhook security (supports GitHub signature and legacy header authentication)
        cls.verify_security_header(request, setting, raw_payload)

        logger.info(
            "Processing webhook request - Webhook: '%s', Project: '%s', "
            "WebhookID: '%s', UserID: '%s', Resource: {type: '%s', id: '%s'}",
            setting.alias,
            setting.project_name,
            webhook_id,
            setting.user_id,
            resource_type,
            resource_id,
        )

        if resource_type == ResourceType.ASSISTANT.value:
            cls.handle_assistant(resource_id, raw_payload, background_tasks, setting.user_id)
        elif resource_type == ResourceType.WORKFLOW.value:
            cls.handle_workflow(resource_id, raw_payload, background_tasks, setting.user_id)
        elif resource_type == ResourceType.DATASOURCE.value:
            await cls.handle_datasource(resource_id, background_tasks, setting.user_id)
        else:
            WebhookMonitoringService.send_webhook_invocation_metric(
                webhook_id=webhook_id,
                project_name=setting.project_name,
                user_id=setting.user_id,
                success=False,
                resource_type=resource_type,
                resource_id=resource_id,
                webhook_alias=setting.alias,
                additional_attributes={"error_cause": "unsupported_resource_type"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=cls.UNSUPPORTED_RESOURCE_TYPE.format(resource_type)
            )

        WebhookMonitoringService.send_webhook_invocation_metric(
            webhook_id=webhook_id,
            project_name=setting.project_name,
            user_id=setting.user_id,
            success=True,
            resource_type=resource_type,
            resource_id=resource_id,
            webhook_alias=setting.alias,
        )
        logger.info(
            "Webhook invocation successful - Webhook: '%s', Project: '%s', "
            "WebhookID: '%s', UserID: '%s', Resource: {type: '%s', id: '%s'}",
            setting.alias,
            setting.project_name,
            webhook_id,
            setting.user_id,
            resource_type,
            resource_id,
        )

        return BaseResponse(message=cls.WEBHOOK_INVOKED_SUCCESSFULLY, data=setting)

    @classmethod
    def _send_verification_metric(
        cls, webhook_id: str, setting, success: bool, verification_method: str, additional_attributes: dict = None
    ):
        """
        Send webhook verification metric (DRY helper).

        Args:
            webhook_id: Webhook identifier
            setting: Webhook settings from SettingsService
            success: Whether verification was successful
            verification_method: Method used for verification
            additional_attributes: Additional attributes to include in the metric
        """
        attrs = additional_attributes or {}
        attrs["verification_method"] = verification_method

        WebhookMonitoringService.send_webhook_invocation_metric(
            webhook_id=webhook_id,
            project_name=setting.project_name,
            user_id=setting.user_id,
            success=success,
            resource_type="verification",
            resource_id=webhook_id,
            webhook_alias=setting.alias,
            additional_attributes=attrs,
        )

    @classmethod
    def verify_security_header(cls, request: Request, setting, raw_payload: bytes):
        """
        Verify webhook security using GitHub signature or legacy header authentication.

        This method supports three security modes (in priority order):
        1. GitHub Webhook Signature Verification (HMAC-SHA256)
        2. Legacy Header-Based Authentication
        3. No Security - Logs warning but allows

        Args:
            request: FastAPI Request object
            setting: Webhook settings from SettingsService
            raw_payload: Raw request body as bytes (required for signature verification)

        Raises:
            HTTPException: If security verification fails
        """
        webhook_id = setting.credential("webhook_id")
        github_secret = setting.credential(cls.GITHUB_WEBHOOK_SECRET)
        is_github_webhook = GitHubWebhookSecurity.is_github_webhook(request)

        # Priority 1: GitHub signature verification
        if github_secret and is_github_webhook:
            return cls._verify_github_signature(request, setting, raw_payload, webhook_id, github_secret)

        # Priority 2: Legacy header authentication
        security_header_name = setting.credential(cls.SECURE_HEADER_NAME)
        security_header_value = setting.credential(cls.SECURE_HEADER_VALUE)

        if security_header_name and security_header_value:
            return cls._verify_legacy_header(request, setting, webhook_id, security_header_name, security_header_value)

        # Priority 3: No security configured (fallback for backward compatibility)
        return cls._handle_no_security(webhook_id, setting)

    @classmethod
    def _verify_github_signature(
        cls, request: Request, setting, raw_payload: bytes, webhook_id: str, github_secret: str
    ):
        """
        Verify GitHub webhook signature and event type.

        Args:
            request: FastAPI Request object
            setting: Webhook settings from SettingsService
            raw_payload: Raw request body as bytes
            webhook_id: Webhook identifier
            github_secret: GitHub webhook secret

        Raises:
            HTTPException: If signature verification fails
        """
        try:
            require_sha256 = setting.credential(cls.GITHUB_REQUIRE_SHA256)
            if require_sha256 is None:
                require_sha256 = True

            GitHubWebhookSecurity.verify_signature(
                request=request, secret=github_secret, payload=raw_payload, require_sha256=require_sha256
            )

            event_filter = setting.credential(cls.GITHUB_EVENT_FILTER)
            if event_filter:
                allowed_events = [e.strip() for e in event_filter.split(',') if e.strip()]
                GitHubWebhookSecurity.validate_event_type(request, allowed_events)

            github_metadata = GitHubWebhookSecurity.extract_github_metadata(request)
            logger.info(
                f"GitHub webhook signature verified successfully. "
                f"WebhookID: '{webhook_id}', Event: {github_metadata.get('event')}, "
                f"Delivery ID: {github_metadata.get('delivery_id')}, "
                f"Project: '{setting.project_name}', UserID: '{setting.user_id}'"
            )

            cls._send_verification_metric(
                webhook_id,
                setting,
                success=True,
                verification_method="github_signature",
                additional_attributes={
                    "event_type": github_metadata.get('event'),
                    "delivery_id": github_metadata.get('delivery_id'),
                },
            )

        except HTTPException as e:
            logger.error(
                f"GitHub webhook signature verification failed. "
                f"WebhookID: '{webhook_id}', Project: '{setting.project_name}', "
                f"UserID: '{setting.user_id}', Error: {e.detail}"
            )
            cls._send_verification_metric(
                webhook_id,
                setting,
                success=False,
                verification_method="github_signature",
                additional_attributes={
                    "error_cause": "github_signature_verification_failed",
                    "status_code": e.status_code,
                },
            )
            raise

    @classmethod
    def _verify_legacy_header(
        cls, request: Request, setting, webhook_id: str, security_header_name: str, security_header_value: str
    ):
        """
        Verify legacy header-based authentication.

        Args:
            request: FastAPI Request object
            setting: Webhook settings from SettingsService
            webhook_id: Webhook identifier
            security_header_name: Name of the security header
            security_header_value: Expected value of the security header

        Raises:
            HTTPException: If header verification fails
        """
        security_header = request.headers.get(security_header_name)

        if not security_header or security_header != security_header_value:
            logger.error(
                f"Invalid security header for webhook. "
                f"WebhookID: '{webhook_id}', Project: '{setting.project_name}', "
                f"UserID: '{setting.user_id}'"
            )
            cls._send_verification_metric(
                webhook_id,
                setting,
                success=False,
                verification_method="legacy_header",
                additional_attributes={"error_cause": "invalid_security_header"},
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=cls.INVALID_SECURITY_HEADER)

        logger.info(
            f"Legacy header authentication successful for webhook '{webhook_id}'. "
            f"Project: '{setting.project_name}', UserID: '{setting.user_id}'"
        )
        cls._send_verification_metric(webhook_id, setting, success=True, verification_method="legacy_header")

    @classmethod
    def _handle_no_security(cls, webhook_id: str, setting):
        """
        Handle webhooks with no security configuration.

        Args:
            webhook_id: Webhook identifier
            setting: Webhook settings from SettingsService
        """
        logger.warning(
            f"Webhook '{webhook_id}' has NO security configuration. "
            f"Project: '{setting.project_name}', UserID: '{setting.user_id}'. "
            "This is not recommended for production environments. "
            "Consider configuring GitHub webhook secret or custom security headers."
        )
        cls._send_verification_metric(
            webhook_id,
            setting,
            success=True,
            verification_method="none",
            additional_attributes={"security_warning": "no_authentication_configured"},
        )

    @classmethod
    def handle_assistant(cls, assistant_id: str, raw_payload: bytes, background_tasks: BackgroundTasks, user_id: str):
        formatted_payload = raw_payload.decode('utf-8')
        assistant = validate_assistant(assistant_id)
        if not assistant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=cls.ASSISTANT_NOT_FOUND.format(assistant_id)
            )

        background_tasks.add_task(
            run_in_thread_pool, invoke_assistant, assistant_id, user_id, assistant_id, formatted_payload
        )

        return BaseResponse(message=cls.WEBHOOK_INVOKED_SUCCESSFULLY, data="")

    @classmethod
    def handle_workflow(cls, workflow_id: str, raw_payload: bytes, background_tasks: BackgroundTasks, user_id: str):
        formatted_payload = raw_payload.decode('utf-8')
        workflow = WorkflowService().get_workflow(workflow_id=workflow_id)
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=cls.WORKFLOW_NOT_FOUND.format(workflow_id)
            )

        background_tasks.add_task(
            run_in_thread_pool, invoke_workflow, workflow_id, user_id, workflow_id, formatted_payload
        )

        return BaseResponse(message=cls.WEBHOOK_INVOKED_SUCCESSFULLY, data="")

    @classmethod
    async def handle_datasource(cls, resource_id, background_tasks: BackgroundTasks, user_id: str):
        datasource = validate_datasource(resource_id)
        if not datasource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=cls.DATASOURCE_NOT_FOUND.format(resource_id)
            )
        project_name = datasource.project_name
        if not datasource.created_by:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Datasource '{resource_id}' is missing creator information.",
            )
        if datasource.project_name != project_name:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Datasource '{resource_id}' project {datasource.project_name} name does not belong"
                + f" to the webhook project '{project_name}'.",
            )

        user = User(id=user_id)
        resource_name = datasource.repo_name
        index_type = datasource.index_type

        if index_type == FullDatasourceTypes.PROVIDER:
            service = ProviderDatasourceReindexService(datasource=datasource, user=user)
            background_tasks.add_task(service.run)
        elif datasource.is_code_index():
            repo_id = GitRepo.identifier_from_fields(
                app_id=project_name, name=resource_name, index_type=CodeIndexType(index_type)
            )
            payload = CodeReindexTask(
                resource_id=resource_id,
                project_name=project_name,
                resource_name=resource_name,
                user=user,
                repo_id=repo_id,
                index_info=datasource,
            )
            background_tasks.add_task(reindex_code, payload)
        elif index_type == FullDatasourceTypes.JIRA:
            jql = datasource.jira.jql if datasource.jira and datasource.jira.jql else ""
            payload = JiraReindexTask(
                resource_id=resource_id,
                project_name=project_name,
                resource_name=resource_name,
                user=user,
                jql=jql,
                index_info=datasource,
            )
            background_tasks.add_task(reindex_jira, payload)
        elif index_type == FullDatasourceTypes.CONFLUENCE:
            if not datasource.confluence:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Confluence datasource '{resource_id}' is missing space keys.",
                )

            payload = ConfluenceReindexTask(
                resource_id=resource_id,
                project_name=project_name,
                resource_name=resource_name,
                user=user,
                confluence_index_info=datasource.confluence,
                index_info=datasource,
            )
            background_tasks.add_task(reindex_confluence, payload)
        elif index_type == FullDatasourceTypes.GOOGLE:
            link = datasource.google_doc_link
            payload = GoogleReindexTask(
                resource_id=resource_id,
                project_name=project_name,
                resource_name=resource_name,
                user=user,
                google_doc_link=link,
                index_info=datasource,
            )
            background_tasks.add_task(reindex_google, payload)

        else:
            raise NotImplementedDatasource(f"Datasource type '{index_type}' is not supported via webhook.")
