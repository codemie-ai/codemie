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
    ConnectionException,
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
    EmptyResultException,
)
from codemie.datasource.confluence_datasource_processor import ConfluenceDatasourceProcessor
from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie.datasource.loader.git_loader import GitBatchLoader
from codemie.datasource.loader.svn_loader import SVNBatchLoader
from codemie.datasource.xray.xray_datasource_processor import XrayDatasourceProcessor
from codemie.datasource.xwiki.xwiki_datasource_processor import XWikiDatasourceProcessor
from codemie.datasource.loader.xwiki_loader import XWikiLoader
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
                case DatasourceTypes.XWIKI:
                    return cls.health_check_xwiki(request, user_id)
                case DatasourceTypes.SVN:
                    return cls.health_check_svn(request, user_id)
                case DatasourceTypes.GIT:
                    return cls.health_check_git(request, user_id)
                case _:
                    return DatasourceHealthCheckResponse(implemented=False)
        except ConnectionException as e:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message=str(e),
                    details=f"An error occurred while checking the connection: {str(e)}",
                    help="Please check the repository URL and credentials, then try again.",
                )
            )
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
    def health_check_xwiki(cls, request: DatasourceHealthCheckRequest, user_id: str):
        if not request.space:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message="xWiki space is required",
                    details="Provide the dotted space id shown in the page URL, for example 'KB'.",
                    help="Open the space in xWiki; the id appears in the URL after /bin/view/.",
                    field_error="space",
                )
            )

        # setting_id must be honoured: the user picks an integration in the form, and without it
        # the check silently validates whichever xWiki integration the project resolves by default.
        xwiki_creds = SettingsService.get_xwiki_creds(
            user_id=user_id,
            project_name=request.project_name,
            setting_id=request.setting_id,
        )

        if xwiki_creds is None:
            # No xWiki integration resolved for the selected setting. Without this guard the None
            # flows into the processor and surfaces as an AttributeError -> HTTP 500 instead of the
            # field error the form expects.
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message="No xWiki integration is configured",
                    details=(
                        "The selected integration could not be resolved for this project. "
                        "Choose an xWiki integration, or add one first."
                    ),
                    help='Open the "Integrations" tab and connect an xWiki integration with a base URL and token.',
                    field_error="setting_id",
                )
            )

        processor = XWikiDatasourceProcessor(
            datasource_name="health_check",
            user=None,  # Not needed for health check
            project_name=request.project_name,
            credentials=xwiki_creds,
            space=request.space,
            wiki=request.wiki or "xwiki",
        )

        try:
            stats = processor._fetch_remote_stats()
        except ConnectionException as e:
            # Handled here rather than by the generic wrapper so the form can point at the URL
            # field: on this failure the base URL is the overwhelmingly likely culprit.
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message=str(e),
                    details=f"An error occurred while checking the connection: {str(e)}",
                    help=(
                        "Check the base URL of the xWiki integration. Some instances serve REST "
                        "at /rest/..., others under /xwiki/rest/... - the URL must match."
                    ),
                    field_error="url",
                )
            )

        documents_count = stats.get(XWikiLoader.DOCUMENTS_COUNT_KEY, 0)

        if stats.get("truncated"):
            return DatasourceHealthCheckResponse(
                documents_count=documents_count,
                error=ErrorMessage(
                    message=(
                        f"Space contains more than {documents_count} pages; "
                        f"only the first {documents_count} would be indexed."
                    ),
                    details="The loader page cap was reached while counting pages.",
                    help="Connect a narrower space, or raise loader_max_pages for the xWiki loader.",
                    field_error="space",
                ),
            )

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
    def health_check_svn(cls, request: DatasourceHealthCheckRequest, user_id: str):
        if not request.svn_repo_url:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message="SVN repository URL is required",
                    details="Provide svn_repo_url in the request to test the SVN connection.",
                    help="Include the SVN repository URL (e.g. https://svn.example.com/repo) in the request.",
                    field_error="svn_repo_url",
                )
            )
        svn_creds = SettingsService.get_svn_creds(
            user_id=user_id,
            project_name=request.project_name,
            repo_link=request.svn_repo_url,
            setting_id=request.setting_id,
        )
        stats = SVNBatchLoader.test_connection(
            url=request.svn_repo_url,
            branch=request.svn_branch or "trunk",
            creds=svn_creds,
        )
        return DatasourceHealthCheckResponse(documents_count=stats.get(SVNBatchLoader.HEAD_REVISION_KEY, 0))

    @classmethod
    def health_check_git(cls, request: DatasourceHealthCheckRequest, user_id: str):
        if not request.git_url:
            return DatasourceHealthCheckResponse(
                error=ErrorMessage(
                    message="Git repository URL is required",
                    details="Provide git_url in the request to test the Git connection.",
                    help="Include the Git repository URL (e.g. https://github.com/owner/repo) in the request.",
                    field_error="git_url",
                )
            )
        if not request.setting_id:
            GitBatchLoader.test_public_access(request.git_url)
            return DatasourceHealthCheckResponse(documents_count=0)

        git_creds = SettingsService.get_git_creds(
            user_id=user_id,
            project_name=request.project_name,
            repo_link=request.git_url,
            setting_id=request.setting_id,
        )
        GitBatchLoader.test_connection(request.git_url, git_creds)
        return DatasourceHealthCheckResponse(documents_count=0)

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
            case DatasourceTypes.XWIKI:
                return "space"
            case _:
                return None
