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

"""Datasources actors."""

import time
from uuid import uuid4

from codemie.configs import logger
from codemie.datasource.datasource_concurrency_manager import datasource_concurrency_manager
from codemie.core.constants import CodeIndexType
from codemie.core.models import GitRepo
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor import (
    AzureDevOpsWikiDatasourceProcessor,
)
from codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor import (
    AzureDevOpsWorkItemDatasourceProcessor,
)
from codemie.datasource.code.code_datasource_processor import CodeDatasourceProcessor
from codemie.datasource.confluence_datasource_processor import (
    ConfluenceDatasourceProcessor,
    IndexKnowledgeBaseConfluenceConfig,
)
from codemie.datasource.google_doc.google_doc_datasource_processor import GoogleDocDatasourceProcessor
from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie.datasource.sharepoint.sharepoint_datasource_processor import (
    SharePointDatasourceProcessor,
    SharePointProcessorConfig,
)
from codemie.rest_api.models.settings import SharePointCredentials
from codemie.datasource.xray.xray_datasource_processor import XrayDatasourceProcessor
from codemie.service.settings.settings import SettingsService
from codemie.triggers.trigger_models import (
    AzureDevOpsWikiReindexTask,
    AzureDevOpsWorkItemReindexTask,
    CodeReindexTask,
    ConfluenceReindexTask,
    GoogleReindexTask,
    JiraReindexTask,
)

REINDEX_START_MSG = "Starting reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s)."
REINDEX_FAILED_MSG = (
    "Failed to start reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s). Error: %s"
)
REINDEX_SUCCESS_MSG = (
    "Successfully initiated reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s)."
)


def reindex_code(payload: CodeReindexTask):
    """
    Initiates the reindexing process for a code datasource.

    Args:
        payload (CodeReindexTask): The task payload containing all necessary information for reindexing a datasource.

    Raises:
        HTTPException: If the specified repository is not found.
    """

    logger.info(
        REINDEX_START_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )

    app_repo = GitRepo.get_by_fields({"id": payload.repo_id, "setting_id": payload.index_info.setting_id})
    if not app_repo:
        error_msg = f"Repository '{payload.resource_name}' not found in applicatioe '{payload.project_name}'."
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    processor = CodeDatasourceProcessor.create_processor(
        git_repo=app_repo,
        user=payload.user,
        index=payload.index_info,
        request_uuid=payload.resource_id,
    )

    datasource_concurrency_manager.run(processor.reprocess, processor.index)

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )


def reindex_jira(payload: JiraReindexTask):
    """
    Initiates the reindexing process for a Jira datasource.

    Args:
        payload (JiraReindexTask): The task payload containing all necessary information for reindexing a datasource.

    Raises:
        HTTPException: If Jira credentials are not found for the project.
    """

    logger.info(
        REINDEX_START_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )

    jira_creds = SettingsService.get_jira_creds(
        user_id=payload.user.id, project_name=payload.project_name, setting_id=payload.index_info.setting_id
    )
    if not jira_creds:
        error_msg = f"Jira credentials not found for project '{payload.project_name}'."
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    processor = JiraDatasourceProcessor(
        datasource_name=payload.resource_name,
        user=payload.user,
        project_name=payload.project_name,
        credentials=jira_creds,
        jql=payload.jql,
        description=payload.index_info.description or "",
        project_space_visible=payload.index_info.project_space_visible or False,
        index_info=payload.index_info,
        request_uuid=payload.resource_id,
        embedding_model=payload.index_info.embeddings_model,
    )

    datasource_concurrency_manager.run(processor.incremental_reindex, processor.index)

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )


def reindex_confluence(payload: ConfluenceReindexTask):
    """
    Initiates the reindexing process for a Confluence datasource.

    Args:
        payload: The task payload containing all necessary information for reindexing a datasource.

    Raises:
        HTTPException: If Confluence credentials or index information are not found.
    """

    logger.info(
        REINDEX_START_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )

    confluence_index_info = (
        payload.index_info.confluence if payload.index_info and payload.index_info.confluence else None
    )
    if not confluence_index_info:
        error_msg = (
            f"Confluence index not found for resource '{payload.resource_name}' in project '{payload.project_name}'."
        )
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    confluence_creds = SettingsService.get_confluence_creds(
        user_id=payload.user.id, project_name=payload.project_name, setting_id=payload.index_info.setting_id
    )
    if not confluence_creds:
        error_msg = f"Confluence credentials not found for project '{payload.project_name}'."
        logger.error(
            REINDEX_FAILED_MSG,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    index_kb_config = IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info(confluence_index_info)

    processor = ConfluenceDatasourceProcessor(
        datasource_name=payload.resource_name,
        user=payload.user,
        project_name=payload.project_name,
        confluence=confluence_creds,
        index_knowledge_base_config=index_kb_config,
        description=payload.index_info.description or "",
        project_space_visible=payload.index_info.project_space_visible or False,
        index=payload.index_info,
        request_uuid=payload.resource_id,
        embedding_model=payload.index_info.embeddings_model,
    )

    datasource_concurrency_manager.run(processor.reprocess, processor.index)

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )


def reindex_google(payload: GoogleReindexTask):
    """
    Initiates the reindexing process for a Google Docs datasource.

    Args:
        payload (GoogleReindexTask): The task payload containing all necessary information for reindexing a Google Docs.

    Raises:
        HTTPException: If Google Docs index information or link is missing.
    """

    logger.info(
        REINDEX_START_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )

    if not payload.index_info.google_doc_link:
        error_msg = (
            f"Google Doc link is missing for resource '{payload.resource_name}' in project '{payload.project_name}'."
        )
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    datasource_processor = GoogleDocDatasourceProcessor(
        datasource_name=payload.index_info.repo_name,
        user=payload.user,
        project_name=payload.index_info.project_name,
        google_doc=payload.index_info.google_doc_link,
        description=payload.index_info.description or "",
        request_uuid=payload.resource_id,
        index_info=payload.index_info,
        embedding_model=payload.index_info.embeddings_model,
    )

    datasource_concurrency_manager.run(datasource_processor.reprocess, datasource_processor.index)

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )


def reindex_azure_devops_wiki(payload: AzureDevOpsWikiReindexTask):
    """
    Initiates the reindexing process for an Azure DevOps Wiki datasource.

    Args:
        payload: The task payload containing all necessary information for reindexing.
    """
    logger.info(
        REINDEX_START_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )

    azure_devops_index_info = (
        payload.index_info.azure_devops_wiki if payload.index_info and payload.index_info.azure_devops_wiki else None
    )
    if not azure_devops_index_info:
        error_msg = (
            f"Azure DevOps Wiki index not found for resource '{payload.resource_name}' "
            f"in project '{payload.project_name}'."
        )
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=payload.user.id,
        project_name=payload.project_name,
    )
    if not azure_devops_creds:
        error_msg = f"Azure DevOps credentials not found for project '{payload.project_name}'."
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    processor = AzureDevOpsWikiDatasourceProcessor(
        datasource_name=payload.resource_name,
        user=payload.user,
        project_name=payload.project_name,
        credentials=azure_devops_creds,
        wiki_query=azure_devops_index_info.wiki_query,
        wiki_name=azure_devops_index_info.wiki_name,
        description=payload.index_info.description or "",
        project_space_visible=payload.index_info.project_space_visible or False,
        index_info=payload.index_info,
        request_uuid=payload.resource_id,
        embedding_model=payload.index_info.embeddings_model,
    )

    datasource_concurrency_manager.run(processor.reprocess, processor.index)

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )


def reindex_azure_devops_work_item(payload: AzureDevOpsWorkItemReindexTask):
    """
    Initiates the reindexing process for an Azure DevOps Work Items datasource.

    Args:
        payload: The task payload containing all necessary information for reindexing.
    """
    logger.info(
        REINDEX_START_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )

    work_item_index_info = payload.azure_devops_work_item_index_info
    if not work_item_index_info:
        error_msg = (
            f"Azure DevOps Work Items index not found for resource '{payload.resource_name}' "
            f"in project '{payload.project_name}'."
        )
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=payload.user.id,
        project_name=payload.project_name,
    )
    if not azure_devops_creds:
        error_msg = f"Azure DevOps credentials not found for project '{payload.project_name}'."
        logger.error(
            REINDEX_FAILED_MSG,
            payload.index_info.index_type,
            payload.resource_id,
            payload.project_name,
            payload.resource_name,
            error_msg,
        )
        return

    processor = AzureDevOpsWorkItemDatasourceProcessor(
        datasource_name=payload.resource_name,
        user=payload.user,
        project_name=payload.project_name,
        credentials=azure_devops_creds,
        wiql_query=work_item_index_info.wiql_query,
        description=payload.index_info.description or "",
        project_space_visible=payload.index_info.project_space_visible or False,
        index_info=payload.index_info,
        request_uuid=payload.resource_id,
        embedding_model=payload.index_info.embeddings_model,
    )

    datasource_concurrency_manager.run(processor.reprocess, processor.index)

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )


UNSUPPORTED_RESUME_TYPES = {"knowledge_base_file", "provider", "platform_marketplace_assistant"}


def _resume_azure_devops(index_info: IndexInfo, user: User, request_uuid: str) -> None:
    """Handle resume for Azure DevOps Wiki and Work Item datasources."""
    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=user.id,
        project_name=index_info.project_name,
    )
    if not azure_devops_creds:
        logger.error(
            f"resume_stale_datasource: Azure DevOps credentials not found for "
            f"project='{index_info.project_name}', index_id={index_info.id}"
        )
        return

    if index_info.index_type == "knowledge_base_azure_devops_wiki":
        azure_devops_index_info = index_info.azure_devops_wiki
        if not azure_devops_index_info:
            logger.error(f"resume_stale_datasource: Azure DevOps Wiki index info missing for index_id={index_info.id}")
            return
        processor = AzureDevOpsWikiDatasourceProcessor(
            datasource_name=index_info.repo_name,
            user=user,
            project_name=index_info.project_name,
            credentials=azure_devops_creds,
            wiki_query=azure_devops_index_info.wiki_query,
            wiki_name=azure_devops_index_info.wiki_name,
            description=index_info.description or "",
            project_space_visible=index_info.project_space_visible or False,
            index_info=index_info,
            request_uuid=request_uuid,
            embedding_model=index_info.embeddings_model,
        )
    else:
        work_item_index_info = index_info.azure_devops_work_item
        if not work_item_index_info:
            logger.error(
                f"resume_stale_datasource: Azure DevOps Work Item index info missing for index_id={index_info.id}"
            )
            return
        processor = AzureDevOpsWorkItemDatasourceProcessor(
            datasource_name=index_info.repo_name,
            user=user,
            project_name=index_info.project_name,
            credentials=azure_devops_creds,
            wiql_query=work_item_index_info.wiql_query,
            description=index_info.description or "",
            project_space_visible=index_info.project_space_visible or False,
            index_info=index_info,
            request_uuid=request_uuid,
            embedding_model=index_info.embeddings_model,
        )
    processor.resume()


def _resume_xray(index_info: IndexInfo, user: User, request_uuid: str) -> None:
    """Handle resume for Xray datasources."""
    xray_creds = SettingsService.get_xray_creds(
        user_id=user.id,
        project_name=index_info.project_name,
        setting_id=index_info.setting_id,
    )
    if not xray_creds:
        logger.error(
            f"resume_stale_datasource: Xray credentials not found for "
            f"project='{index_info.project_name}', index_id={index_info.id}"
        )
        return
    XrayDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=user,
        project_name=index_info.project_name,
        credentials=xray_creds,
        jql=index_info.xray.jql if index_info.xray else "",
        description=index_info.description or "",
        project_space_visible=index_info.project_space_visible or False,
        index_info=index_info,
        request_uuid=request_uuid,
        embedding_model=index_info.embeddings_model,
    ).resume()


def _get_sharepoint_oauth_creds(sp_index_info, index_info: IndexInfo) -> SharePointCredentials | None:
    """Resolve OAuth credentials for a SharePoint index; return None if token is missing/expired."""
    if not sp_index_info.access_token or sp_index_info.expires_at <= int(time.time()):
        logger.warning(
            f"resume_stale_datasource: SharePoint index_id={index_info.id} uses "
            f"auth_type='{sp_index_info.auth_type}' but stored token is missing or expired, skipping"
        )
        return None
    from codemie.datasource.sharepoint.sharepoint_datasource_processor import _decrypt_oauth_token

    return SharePointCredentials(
        auth_type="oauth",
        access_token=_decrypt_oauth_token(sp_index_info.access_token),
        expires_at=sp_index_info.expires_at,
    )


def _resume_sharepoint(index_info: IndexInfo, user: User, request_uuid: str) -> None:
    """Handle resume for SharePoint datasources."""
    sp_index_info = index_info.sharepoint
    if not sp_index_info:
        logger.error(f"resume_stale_datasource: SharePoint index info missing for index_id={index_info.id}")
        return

    if sp_index_info.auth_type in ("oauth_codemie", "oauth_custom"):
        sharepoint_creds = _get_sharepoint_oauth_creds(sp_index_info, index_info)
        if sharepoint_creds is None:
            return
    else:
        try:
            sharepoint_creds = SettingsService.get_sharepoint_creds(
                user_id=user.id,
                project_name=index_info.project_name,
                setting_id=index_info.setting_id,
            )
        except ValueError:
            logger.error(
                f"resume_stale_datasource: SharePoint credentials not found for "
                f"project='{index_info.project_name}', index_id={index_info.id}"
            )
            return
    SharePointDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=user,
        project_name=index_info.project_name,
        credentials=sharepoint_creds,
        sp_config=SharePointProcessorConfig(
            site_url=sp_index_info.site_url,
            path_filter=sp_index_info.path_filter or "*",
            include_pages=sp_index_info.include_pages if sp_index_info.include_pages is not None else True,
            include_documents=sp_index_info.include_documents if sp_index_info.include_documents is not None else True,
            include_lists=sp_index_info.include_lists if sp_index_info.include_lists is not None else True,
            max_file_size_mb=sp_index_info.max_file_size_mb or 50,
            files_filter=sp_index_info.files_filter or "",
            auth_type=sp_index_info.auth_type,
            oauth_client_id=sp_index_info.oauth_client_id,
            oauth_tenant_id=sp_index_info.oauth_tenant_id,
            description=index_info.description or "",
            project_space_visible=index_info.project_space_visible or False,
        ),
        setting_id=index_info.setting_id,
        embedding_model=index_info.embeddings_model,
        index_info=index_info,
        request_uuid=request_uuid,
    ).resume()


def _resume_code(index_info: IndexInfo, user: User, request_uuid: str) -> None:
    """Handle resume for code datasources."""
    repo_id = GitRepo.identifier_from_fields(
        app_id=index_info.project_name,
        name=index_info.repo_name,
        index_type=CodeIndexType(index_info.index_type),
    )
    app_repo = GitRepo.get_by_fields({"id": repo_id, "setting_id": index_info.setting_id})
    if not app_repo:
        logger.error(
            f"resume_stale_datasource: GitRepo not found for "
            f"project='{index_info.project_name}', repo='{index_info.repo_name}', "
            f"setting_id={index_info.setting_id}"
        )
        return
    CodeDatasourceProcessor.create_processor(
        git_repo=app_repo,
        user=user,
        index=index_info,
        request_uuid=request_uuid,
    ).resume()


def _resume_jira(index_info: IndexInfo, user: User, request_uuid: str) -> None:
    """Handle resume for Jira datasources."""
    jira_creds = SettingsService.get_jira_creds(
        user_id=user.id,
        project_name=index_info.project_name,
        setting_id=index_info.setting_id,
    )
    if not jira_creds:
        logger.error(
            f"resume_stale_datasource: Jira credentials not found for "
            f"project='{index_info.project_name}', index_id={index_info.id}"
        )
        return
    JiraDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=user,
        project_name=index_info.project_name,
        credentials=jira_creds,
        jql=index_info.jira.jql if index_info.jira else "",
        description=index_info.description or "",
        project_space_visible=index_info.project_space_visible or False,
        index_info=index_info,
        request_uuid=request_uuid,
        embedding_model=index_info.embeddings_model,
    ).resume()


def _resume_confluence(index_info: IndexInfo, user: User, request_uuid: str) -> None:
    """Handle resume for Confluence datasources."""
    confluence_index_info = index_info.confluence
    if not confluence_index_info:
        logger.error(f"resume_stale_datasource: Confluence index info missing for index_id={index_info.id}")
        return
    confluence_creds = SettingsService.get_confluence_creds(
        user_id=user.id,
        project_name=index_info.project_name,
        setting_id=index_info.setting_id,
    )
    if not confluence_creds:
        logger.error(
            f"resume_stale_datasource: Confluence credentials not found for "
            f"project='{index_info.project_name}', index_id={index_info.id}"
        )
        return
    ConfluenceDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=user,
        project_name=index_info.project_name,
        confluence=confluence_creds,
        index_knowledge_base_config=IndexKnowledgeBaseConfluenceConfig.from_confluence_index_info(
            confluence_index_info
        ),
        description=index_info.description or "",
        project_space_visible=index_info.project_space_visible or False,
        index=index_info,
        request_uuid=request_uuid,
        embedding_model=index_info.embeddings_model,
    ).resume()


def _resume_google_doc(index_info: IndexInfo, user: User, request_uuid: str) -> None:
    """Handle resume for Google Doc datasources."""
    if not index_info.google_doc_link:
        logger.error(f"resume_stale_datasource: Google Doc link missing for index_id={index_info.id}")
        return
    GoogleDocDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=user,
        project_name=index_info.project_name,
        google_doc=index_info.google_doc_link,
        description=index_info.description or "",
        request_uuid=request_uuid,
        index_info=index_info,
        embedding_model=index_info.embeddings_model,
    ).resume()


_RESUME_DISPATCH: dict = {
    "code": _resume_code,
    "knowledge_base_jira": _resume_jira,
    "knowledge_base_confluence": _resume_confluence,
    "llm_routing_google": _resume_google_doc,
    "knowledge_base_azure_devops_wiki": _resume_azure_devops,
    "knowledge_base_azure_devops_work_item": _resume_azure_devops,
    "knowledge_base_xray": _resume_xray,
    "knowledge_base_sharepoint": _resume_sharepoint,
}


def resume_stale_datasource(index_info: IndexInfo) -> None:
    """Resume a stuck in-progress datasource index job detected by the watchdog."""
    index_type = index_info.index_type

    if index_type in UNSUPPORTED_RESUME_TYPES:
        logger.warning(
            f"Skipping stale resume for unsupported index type '{index_type}' "
            f"(index_id={index_info.id}, repo='{index_info.repo_name}')"
        )
        return

    if not index_info.created_by or not index_info.created_by.id:
        logger.error(f"resume_stale_datasource: created_by missing for index_id={index_info.id}, skipping")
        return

    handler = _RESUME_DISPATCH.get(index_type)
    if handler:
        handler(index_info, User(id=index_info.created_by.id), str(uuid4()))
    else:
        logger.warning(
            f"resume_stale_datasource: unrecognised index type '{index_type}', skipping (index_id={index_info.id})"
        )
