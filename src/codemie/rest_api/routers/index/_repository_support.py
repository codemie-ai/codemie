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


from fastapi import BackgroundTasks, status

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    Application,
    BaseResponse,
    GitRepo,
    SVNRepo,
)
from codemie.datasource.svn.svn_index_service import SVNIndexService
from codemie.rest_api.models.index import (
    IndexInfo,
    UpdateIndexRequest,
)
from codemie.rest_api.security.user import User
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService

from codemie.rest_api.routers.index._common import (
    _update_datasource_scheduler,
    _validate_project_change,
)


SVN_REPOSITORY_NOT_FOUND_MESSAGE = "Repository not found"


_REPO_FIELD_MAP = {
    'branch': 'branch',
    'filesFilter': 'files_filter',
    'link': 'link',
    'setting_id': 'setting_id',
    'docsGeneration': 'docs_generation',
    'embeddingsModel': 'embeddings_model',
    'projectSpaceVisible': 'project_space_visible',
}


_INDEX_FIELD_MAP = {
    'description': 'description',
    'prompt': 'prompt',
    'docsGeneration': 'docs_generation',
    'filesFilter': 'files_filter',
    'projectSpaceVisible': 'project_space_visible',
    'embeddingsModel': 'embeddings_model',
    'branch': 'branch',
    'link': 'link',
    'setting_id': 'setting_id',
}


def _update_svn_index_and_repo_fields(
    index_info: IndexInfo,
    request: UpdateIndexRequest,
    user: User,
    svn_repo: SVNRepo,
) -> None:
    index_info.update_index(
        user=user,
        description=request.description,
        prompt=request.prompt,
        project_space_visible=request.projectSpaceVisible,
        docs_generation=request.docsGeneration,
        embeddings_model=request.embeddingsModel,
        files_filter=request.filesFilter,
        branch=request.branch,
        link=request.link,
        reset_error=False,
        setting_id=request.setting_id,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )
    repo_updates = {
        "branch": request.branch,
        "link": request.link.strip() if request.link else None,
        "files_filter": request.filesFilter,
        "setting_id": request.setting_id,
        "docs_generation": request.docsGeneration,
        "embeddings_model": request.embeddingsModel,
        "project_space_visible": request.projectSpaceVisible,
    }
    for attr, value in repo_updates.items():
        if value is not None:
            setattr(svn_repo, attr, value)
    svn_repo.update()


def _handle_index_and_repository_update(
    index_info: IndexInfo,
    request: UpdateIndexRequest,
    user: User,
    app_name: str,
    repo_name: str,
    repository: GitRepo,
) -> None:
    """Handle index information update, metrics, and repository field updates."""
    _validate_project_change(request.new_project_name, app_name, repo_name, user)

    index_patch = {
        attr: getattr(request, req) for req, attr in _INDEX_FIELD_MAP.items() if req in request.model_fields_set
    }
    index_info.update_index(
        user=user,
        **index_patch,
        reset_error=False,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )
    AgentMonitoringService.send_count_metric(
        name="update_datasource",
        attributes={
            "datasource_type": index_info.index_type,
            "project": index_info.project_name,
            "repo_name": index_info.repo_name,
            "user_name": user.username,
            "user_id": user.id,
        },
    )

    for req_field, repo_attr in _REPO_FIELD_MAP.items():
        if req_field in request.model_fields_set:
            value = getattr(request, req_field)
            if value is None:
                continue
            if req_field == 'link' and value:
                value = value.strip()
            setattr(repository, repo_attr, value)
    repository.update()


def _get_repository_with_project_change(
    application: Application,
    repo_name: str,
    index_info: IndexInfo,
    request: UpdateIndexRequest,
) -> GitRepo:
    """Get repository and handle project change if needed."""
    app_repositories = GitRepo.get_by_app_id(application.name)
    repository = next((repo for repo in app_repositories if repo.name == repo_name), None)

    if repository and request.new_project_name and repository.app_id != request.new_project_name:
        try:
            new_repo = GitRepo(
                **repository.model_dump(exclude={"app_id", "id", "original_storage"}),
                app_id=request.new_project_name,
            )
            new_repo.id = new_repo.get_identifier()
            new_repo.original_storage = repository.get_identifier()
            new_repo.save()
            repository = new_repo
        except Exception:
            logger.error(f"Repository already exist for new project name {request.new_project_name}")

    if not repository:
        all_repos_with_name = GitRepo.get_all_by_fields({"name": repo_name})
        repository = next(
            (r for r in all_repos_with_name if r.link == index_info.link),
            None,
        )

    if not repository:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=SVN_REPOSITORY_NOT_FOUND_MESSAGE,
            details=f"The repository with name '{repo_name}' could not be found "
            "in the list of application repositories.",
            help="Please verify the repository name and ensure it exists within the application."
            " If you believe this is an error, check the application configuration or contact support.",
        )

    return repository


def _handle_svn_reindex(
    app_name: str,
    repo_name: str,
    index_info: IndexInfo,
    request: UpdateIndexRequest,
    request_uuid: str,
    tasks: BackgroundTasks,
    user: User,
    full_reindex: bool,
    skip_reindex: bool,
    resume_indexing: bool,
    cron_expression_provided: bool,
) -> BaseResponse:
    svn_repos = SVNRepo.get_by_app_id(app_id=app_name)
    svn_repo = next((r for r in svn_repos if r.name == repo_name), None)
    if not svn_repo:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=SVN_REPOSITORY_NOT_FOUND_MESSAGE,
            details=f"The SVN repository '{repo_name}' could not be found in project '{app_name}'.",
            help="Please verify the repository name and ensure it exists within the project.",
        )

    if skip_reindex:
        SVNIndexService.validate_credentials(
            user_id=user.id,
            project_name=index_info.project_name,
            repo_link=svn_repo.link,
            setting_id=svn_repo.setting_id,
        )
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, index_info, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=f"{repo_name} updated successfully!")

    SVNIndexService.reindex(
        svn_repo=svn_repo,
        index_info=index_info,
        user=user,
        app_name=app_name,
        repo_name=repo_name,
        request_uuid=request_uuid,
        background_tasks=tasks,
        resume_indexing=resume_indexing,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression if cron_expression_provided else None,
        timezone=request.timezone,
    )

    if resume_indexing:
        return BaseResponse(message=f"Indexing of datasource {repo_name} has been resumed in the background")
    reindex_type = "Full" if full_reindex else "Incremental"
    return BaseResponse(
        message=f"{reindex_type} reindexing of datasource {request.name} has been started in the background"
    )
