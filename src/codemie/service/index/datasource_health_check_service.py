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

from codemie.core.constants import DatasourceTypes
from codemie.datasource.exceptions import (
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
    EmptyResultException,
)
from codemie.datasource.confluence_datasource_processor import ConfluenceDatasourceProcessor
from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie.datasource.xray.xray_datasource_processor import XrayDatasourceProcessor
from codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor import (
    AzureDevOpsWikiDatasourceProcessor,
)
from codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor import (
    AzureDevOpsWorkItemDatasourceProcessor,
)
from codemie.rest_api.models.index import ErrorMessage, DatasourceHealthCheckRequest, DatasourceHealthCheckResponse
from codemie.service.settings.settings import SettingsService


class IndexHealthCheckService:
    @classmethod
    def health_check_datasource(cls, request: DatasourceHealthCheckRequest, user_id: str):
        try:
            match request.index_type:
                case DatasourceTypes.JIRA:
                    return cls.health_check_jira(request, user_id)
                case DatasourceTypes.XRAY:
                    return cls.health_check_xray(request, user_id)
                case DatasourceTypes.CONFLUENCE:
                    return cls.health_check_confluence(request, user_id)
                case DatasourceTypes.AZURE_DEVOPS_WIKI:
                    return cls.health_check_azure_devops_wiki(request, user_id)
                case DatasourceTypes.AZURE_DEVOPS_WORK_ITEM:
                    return cls.health_check_azure_devops_work_item(request, user_id)
                case _:
                    return DatasourceHealthCheckResponse(implemented=False)
        except MissingIntegrationException as e:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message=str(e),
                    details=f"An error occurred while checking the integration: {str(e)}",
                    help="Please check missing URL or token in \"Integrations\" tab  and try again.",
                )
            )
        except InvalidQueryException as e:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message=str(e),
                    details=f"An error occurred while trying to load data by given query: {str(e)}",
                    help="Please check your JQL/CQL expression",
                    field_error=cls.get_invalid_field(request.index_type),
                )
            )
        except UnauthorizedException as e:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message=str(e),
                    details=f"An error occurred while trying to authenticate with provided integration: {str(e)}",
                    help="Please check your token in \"Integrations\" tab is correct and JQL/CQL expression is valid.",
                )
            )
        except EmptyResultException as e:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message=str(e),
                    details=f"An empty result returned while trying to load data with provided expression: {str(e)}",
                    help="Please check provided expression.",
                    field_error=cls.get_invalid_field(request.index_type),
                )
            )

    @classmethod
    def health_check_jira(cls, request: DatasourceHealthCheckRequest, user_id: str):
        jira_creds = SettingsService.get_jira_creds(
            user_id=user_id,
            project_name=request.project_name,
            setting_id=request.setting_id,
        )

        return DatasourceHealthCheckResponse(
            documents_count=JiraDatasourceProcessor.check_jira_query(jql=request.jql, credentials=jira_creds)
        )

    @classmethod
    def health_check_xray(cls, request: DatasourceHealthCheckRequest, user_id: str):
        xray_creds = SettingsService.get_xray_creds(
            user_id=user_id,
            project_name=request.project_name,
            setting_id=request.setting_id,
        )

        return DatasourceHealthCheckResponse(
            documents_count=XrayDatasourceProcessor.check_xray_query(jql=request.jql, credentials=xray_creds)
        )

    @classmethod
    def health_check_confluence(cls, request: DatasourceHealthCheckRequest, user_id: str):
        confluence_creds = SettingsService.get_confluence_creds(
            user_id=user_id,
            project_name=request.project_name,
            setting_id=request.setting_id,
        )

        return DatasourceHealthCheckResponse(
            documents_count=ConfluenceDatasourceProcessor.check_confluence_query(
                cql=request.cql,
                confluence=confluence_creds,
            )
        )

    @classmethod
    def health_check_azure_devops_wiki(cls, request: DatasourceHealthCheckRequest, user_id: str):
        azure_devops_creds = SettingsService.get_azure_devops_creds(
            user_id=user_id,
            project_name=request.project_name,
        )

        processor = AzureDevOpsWikiDatasourceProcessor(
            datasource_name="health_check",
            user=None,  # Not needed for health check
            project_name=request.project_name,
            credentials=azure_devops_creds,
            wiki_query=request.wiki_query if hasattr(request, "wiki_query") else "*",
            wiki_name=request.wiki_name if hasattr(request, "wiki_name") else None,
        )

        documents_count = processor._check_docs_health()
        return DatasourceHealthCheckResponse(documents_count=documents_count)

    @classmethod
    def health_check_azure_devops_work_item(cls, request: DatasourceHealthCheckRequest, user_id: str):
        azure_devops_creds = SettingsService.get_azure_devops_creds(
            user_id=user_id,
            project_name=request.project_name,
        )

        processor = AzureDevOpsWorkItemDatasourceProcessor(
            datasource_name="health_check",
            user=None,  # Not needed for health check
            project_name=request.project_name,
            credentials=azure_devops_creds,
            wiql_query=(
                request.wiql_query or "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"
            ),
        )

        documents_count = processor._check_docs_health()
        return DatasourceHealthCheckResponse(documents_count=documents_count)

    @classmethod
    def get_invalid_field(cls, index_type: str):
        match index_type:
            case DatasourceTypes.JIRA:
                return "jql"
            case DatasourceTypes.XRAY:
                return "jql"
            case DatasourceTypes.CONFLUENCE:
                return "cql"
            case DatasourceTypes.AZURE_DEVOPS_WIKI:
                return "wiki_query"
            case DatasourceTypes.AZURE_DEVOPS_WORK_ITEM:
                return "wiql_query"
            case _:
                return None
