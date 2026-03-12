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

from codemie.configs import logger
from codemie.core.models import GitRepo
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

    processor.reprocess()

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

    processor.incremental_reindex()

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

    processor.reprocess()

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

    datasource_processor.reprocess()

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

    processor.reprocess()

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

    processor.reprocess()

    logger.info(
        REINDEX_SUCCESS_MSG,
        payload.index_info.index_type,
        payload.resource_id,
        payload.project_name,
        payload.resource_name,
    )
