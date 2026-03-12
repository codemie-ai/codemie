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

from functools import lru_cache
import math
from typing import Any, Dict, Optional

from codemie.configs import logger
from codemie.configs.config import config
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.core.ability import Ability
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.datasource.loader.git_loader import GitBatchLoader
from codemie.rest_api.models.index import IndexInfo, IndexListItem, SortKey, SortOrder
from codemie.rest_api.models.provider import Provider, ProviderAiceDatasource
from codemie.rest_api.security.user import User
from codemie.service.constants import FullDatasourceTypes
from codemie.service.filter.filter_services import IndexInfoFilter
from sqlmodel import select, or_, and_, Session, func
from sqlalchemy.orm import load_only

MAX_ITEMS_PER_PAGE = 10_000


# Initialize provider IDs at module level for performance, with graceful handling
# for environments where the database is unavailable (e.g., unit tests, CI pipelines).
@lru_cache()
def get_provider_id(name: str) -> Optional[str]:
    provider = Provider.get_by_fields({"name": name})
    provider_id = getattr(provider, "id", None)
    return provider_id


class IndexStatusService:
    @staticmethod
    def get_index_status_markdown(datasource: IndexInfo):
        content = "# Data Source Information\n"
        content += f"### Name: {datasource.repo_name or datasource.full_name}\n"
        content += f"#### Source Type: {datasource.index_type}\n"
        content += f"#### Description: {datasource.description}\n"
        content += IndexStatusService.get_tokens_usage_markdown(datasource)
        content += IndexStatusService.get_configuration_markdown(datasource)
        content += IndexStatusService.get_processing_summary_markdown(datasource)

        if datasource.is_code_index():
            content += IndexStatusService.get_unique_extensions_markdown(datasource)
            content += IndexStatusService.get_filter_markdown(datasource.files_filter)
            content += IndexStatusService.get_filtered_documents_markdown(datasource)

        content += IndexStatusService.get_processed_documents_markdown(datasource)
        return content

    @staticmethod
    def get_tokens_usage_markdown(datasource: IndexInfo):
        if not datasource.tokens_usage:
            return ""

        content = "### Tokens usage\n"
        content += f"#### Input tokens: {datasource.tokens_usage.input_tokens}\n"
        content += f"#### Output Tokens: {datasource.tokens_usage.output_tokens}\n"
        content += f"#### Money spent: ${datasource.tokens_usage.money_spent}\n"
        return content

    @staticmethod
    def get_configuration_markdown(datasource: IndexInfo):
        if datasource.index_type == FullDatasourceTypes.PROVIDER or datasource.index_type == FullDatasourceTypes.FILE:
            return ""

        content = "### Data Source Configuration\n"

        if datasource.is_code_index():
            content += f"#### Repository Link: {datasource.link}\n"
            content += f"#### Branch: {datasource.branch}\n"
            content += f"#### Embeddings Model: {datasource.embeddings_model}\n"
        elif datasource.index_type == FullDatasourceTypes.GOOGLE:
            content += f"#### Link to google docs: {datasource.google_doc_link}\n"
        elif datasource.index_type == FullDatasourceTypes.CONFLUENCE:
            content += f"#### CQL: {datasource.confluence.cql}\n"
        elif datasource.index_type == FullDatasourceTypes.JIRA:
            content += f"#### JQL: {datasource.jira.jql}\n"

        return content

    @staticmethod
    def get_processing_summary_markdown(datasource):
        if datasource.index_type == FullDatasourceTypes.PROVIDER:
            return ""

        content = "### Processing Summary\n"
        total_docs = datasource.processing_info.get(BaseDatasourceLoader.TOTAL_DOCUMENTS_KEY, 0)
        content += f"#### Total Documents: {total_docs}\n"
        document_count = datasource.processing_info.get(BaseDatasourceLoader.DOCUMENTS_COUNT_KEY, 0)
        content += f"#### Processed Documents Count: {document_count}\n"
        content += f"#### Imported Chunks Count: {datasource.current__chunks_state}\n"
        skipped_docs = datasource.processing_info.get(BaseDatasourceLoader.SKIPPED_DOCUMENTS_KEY, 0)
        content += f"#### Skipped Documents Count: {skipped_docs}\n"

        if datasource.is_code_index():
            content += f"#### Total Size (KB): {datasource.processing_info[GitBatchLoader.TOTAL_SIZE_KB_KEY]}\n"
            content += (
                f"#### Avg File Size (Bytes): {datasource.processing_info[GitBatchLoader.AVERAGE_FILE_SIZE_KEY]}\n"
            )
        return content

    @staticmethod
    def _generate_list_markdown(title: str, items: list, header_level: int = 3) -> str:
        header_prefix = "#" * header_level
        content = f"{header_prefix} {title}:\n"
        for item in sorted(items):
            if item:
                content += f" - {item}\n"
        return content

    @staticmethod
    def get_filter_markdown(files_filter: str) -> str:
        filters = files_filter.split('\n')
        return IndexStatusService._generate_list_markdown("Filter", filters, 3)

    @staticmethod
    def get_unique_extensions_markdown(datasource: IndexInfo) -> str:
        extensions = datasource.processing_info[GitBatchLoader.UNIQUE_EXTENSIONS_KEY]
        return IndexStatusService._generate_list_markdown("Unique Extensions", extensions, 4)

    @staticmethod
    def get_processed_documents_markdown(datasource: IndexInfo) -> str:
        if datasource.is_code_index():
            res = ElasticSearchClient.get_client().search(
                index=datasource.get_index_identifier(),
                query={"match_all": {}},
                source=["metadata.file_path"],
                size=MAX_ITEMS_PER_PAGE,
            )
            processed_documents = [hit['_source']['metadata']['file_path'] for hit in res['hits']['hits']]
            processed_documents = list(set(processed_documents))
        else:
            processed_documents = datasource.processed_files
        return IndexStatusService._generate_list_markdown("Processed Documents", processed_documents, 3)

    @staticmethod
    def get_filtered_documents_markdown(index: IndexInfo) -> str:
        documents = index.processing_info[GitBatchLoader.FILTERED_DOCUMENTS_KEY]
        return IndexStatusService._generate_list_markdown("Skipped Documents", documents, 3)

    @classmethod
    def get_index_info_list(
        cls,
        user: User,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 0,
        per_page: int = MAX_ITEMS_PER_PAGE,
        sort_key: Optional[SortKey] = SortKey.DATE,
        sort_order: Optional[SortOrder] = SortOrder.DESC,
        full_response: bool = False,
    ):
        """
        Get a list of index information with pagination and filtering.
        :param full_response: If True, return all fields in the response, otherwise return only essential fields.
        """
        # Base query
        # Select only columns defined in response_class
        response_wrapper = IndexInfo if full_response else IndexListItem
        columns = [getattr(IndexInfo, field) for field in response_wrapper.model_fields if hasattr(IndexInfo, field)]

        # Add provider_fields if not already present
        if IndexInfo.provider_fields not in columns:
            columns.append(IndexInfo.provider_fields)

        statement = select(IndexInfo).options(load_only(*columns))

        # Add user filter
        if not user.is_admin:
            statement = statement.where(cls._owned_by_user_filter(user=user))

        if filters:
            statement = IndexInfoFilter.add_sql_filters(
                query=statement, model_class=IndexInfo, raw_filters=filters, is_admin=user.is_admin
            )

        with Session(IndexInfo.get_engine()) as session:
            # Count total before pagination
            total = session.exec(select(func.count()).select_from(statement.subquery())).one()

            # Add sorting
            sort_column = getattr(IndexInfo, sort_key)
            statement = (
                statement.order_by(sort_column.desc().nullslast())
                if sort_order == SortOrder.DESC
                else statement.order_by(sort_column.asc().nullslast())
            )

            # Add pagination
            statement = statement.offset(page * per_page)
            statement = statement.limit(per_page)

            index_list = session.exec(statement).all()

        # Add user abilities to each result
        index_list_response = []
        for item in index_list:
            item.user_abilities = Ability(user).list(item)
            index_list_item = response_wrapper(**item.model_dump())

            # Set aice_datasource_id for CodeExplorationServiceProvider indexes
            if hasattr(index_list_item, "aice_datasource_id"):
                index_list_item.aice_datasource_id = cls._get_code_exploration_service_datasource_id(item)

            index_list_response.append(index_list_item)

        pages = math.ceil(total / per_page)
        meta = {"page": page, "per_page": per_page, "total": total, "pages": pages}

        return {"data": index_list_response, "pagination": meta}

    @classmethod
    def get_aice_datasources(cls, user: User):
        """
        Get a list of CodeAnalysisServiceProvider indexes with their external data source ids
        """
        provider_id = get_provider_id(config.CODE_ANALYSIS_SERVICE_PROVIDER_NAME)
        if not provider_id:
            return []

        statement = select(IndexInfo).where(
            IndexInfo.get_field_expression("provider_fields.provider_id") == provider_id
        )
        # Add user filter
        if not user.is_admin:
            statement = statement.where(cls._owned_by_user_filter(user=user))
        statement = statement.order_by(IndexInfo.repo_name)

        with Session(IndexInfo.get_engine()) as session:
            index_list = session.exec(statement).all()

        response = [
            ProviderAiceDatasource(
                name=index_info.repo_name,
                datasource_id=index_info.provider_fields.base_params.get('datasource_id', None),
                project_name=index_info.project_name,
            )
            for index_info in index_list
        ]

        return response

    @classmethod
    def get_users(cls, user: User):
        """Return distinct list of users who created indexes"""
        statement = select(IndexInfo.created_by).distinct()

        # Add user filter for non-admin users
        if not user.is_admin:
            statement = statement.where(cls._owned_by_user_filter(user=user))

        with Session(IndexInfo.get_engine()) as session:
            results = session.exec(statement).all()
            return results

    @classmethod
    def _owned_by_user_filter(cls, user):
        """Return index statuses created by the user"""
        return or_(
            and_(IndexInfo.project_name.in_(user.project_names), IndexInfo.project_space_visible),
            IndexInfo.project_name.in_(user.admin_project_names),
            IndexInfo.created_by['id'].astext == user.id,
        )

    @classmethod
    def belongs_to_project(cls, datasource_id: str, project_name: str) -> bool:
        """
        Verify if a datasource with the given ID belongs to the specified project.

        Args:
            datasource_id: The ID of the datasource to verify
            project_name: The name of the project to check against

        Returns:
            bool: True if the datasource belongs to the project, False otherwise
        """
        try:
            datasource = IndexInfo.find_by_id(datasource_id)

            return datasource and datasource.project_name == project_name
        except Exception:
            return False

    @classmethod
    def _get_code_exploration_service_datasource_id(cls, index: IndexInfo):
        """Return datasource_id for CodeExplorationServiceProvider indexes"""
        provider_id = get_provider_id(config.CODE_EXPLORATION_SERVICE_PROVIDER_NAME)
        if not index.provider_fields or index.provider_fields.provider_id != provider_id:
            return None

        base_params = getattr(index.provider_fields, 'base_params', None)
        datasource_id = base_params.get('datasource_id') if base_params else None
        return datasource_id

    @classmethod
    def enrich_index_with_schedule(cls, index: IndexInfo, user: User) -> Dict[str, Any]:
        """
        Enrich index information with cron expression from scheduler settings.

        Args:
            index: IndexInfo object to enrich
            user: User object for fetching scheduler settings

        Returns:
            Dict containing index data with cron_expression field added
        """
        from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

        try:
            scheduler_map = SchedulerSettingsService.get_scheduler_settings_for_datasources(user.id, [str(index.id)])
            cron_expression = scheduler_map.get(str(index.id))
        except Exception as e:
            logger.error(f"Failed to fetch scheduler settings for index {index.id}: {e}", exc_info=True)
            cron_expression = None

        # Convert to dict and add cron_expression
        index_dict = index.model_dump()
        index_dict["cron_expression"] = cron_expression

        return index_dict
