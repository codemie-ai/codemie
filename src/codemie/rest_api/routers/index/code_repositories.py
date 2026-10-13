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

from typing import List, Optional
from typing_extensions import Annotated
from pydantic import StringConstraints

from elasticsearch.exceptions import NotFoundError
from fastapi import APIRouter, BackgroundTasks, status, Depends
from starlette.requests import Request

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    Application,
    BaseResponse,
    GitRepo,
    BaseGitRepo,
    SVNRepo,
    BaseRepository,
    SVN_LINK_PATTERN,
    BRANCH_PATTERN,
)
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.datasource.code.code_datasource_processor import (
    index_code_datasource_in_background,
    update_code_datasource_in_background,
    run_in_background,
)
from codemie.datasource.svn.svn_datasource_processor import SVNDatasourceProcessor
from codemie.datasource.svn.svn_index_service import SVNIndexService
from codemie.rest_api.models.index import (
    CronExpressionValidatorMixin,
    IndexInfo,
    CodeIndexInfo,
    UpdateIndexRequest,
)
from codemie.rest_api.security.authentication import authenticate, application_access_check
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.rest_api.routers.utils import (
    raise_forbidden,
)
from codemie.service.request_summary_manager import request_summary_manager

from codemie.rest_api.routers.index._common import (
    APPLICATION_NOT_FOUND_MESSAGE,
    APPLICATION_NOT_FOUND_HELP,
    _update_datasource_scheduler,
    _index_unique_check,
    _validate_git_credentials,
    _validate_project_change,
)
from codemie.rest_api.routers.index._repository_support import (
    SVN_REPOSITORY_NOT_FOUND_MESSAGE,
    _update_svn_index_and_repo_fields,
    _handle_index_and_repository_update,
    _get_repository_with_project_change,
    _handle_svn_reindex,
)


router = APIRouter()


class CreateIndexRequest(CronExpressionValidatorMixin, BaseGitRepo):
    """
    Request model for creating a new code index with optional guardrail assignments.

    Solves circular import issue within the BaseGitRepo file.
    """

    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class CreateSVNIndexRequest(CronExpressionValidatorMixin, BaseRepository):
    """Request model for creating a new SVN code index."""

    link: Annotated[str, StringConstraints(pattern=SVN_LINK_PATTERN, min_length=1, max_length=1000, strict=True)]
    branch: Annotated[str, StringConstraints(pattern=BRANCH_PATTERN, min_length=1, max_length=1000, strict=True)] = (
        "trunk"
    )
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


@router.post(
    "/application/{app_name}/index",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(application_access_check)],
    response_model=BaseResponse,
)
def create_index_application(
    app_name: str,
    create_git_repo_request: CreateIndexRequest,
    request: Request,
    tasks: BackgroundTasks,
    user: User = Depends(authenticate),
):
    request_uuid = request.state.uuid
    request_summary_manager.create_request_summary(
        request_id=request_uuid,
        project_name=app_name,
        user=user.as_user_model(),
    )
    cron_expression_provided = 'cron_expression' in create_git_repo_request.model_fields_set

    if request.state.user.is_demo_user:
        existing_repos = CodeIndexInfo.get_by_user_id(user_id=request.state.user.id)

        if len(existing_repos) >= 1:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Repository limit reached",
                details="Demo users are allowed to index only one repository. You have already reached this limit.",
                help="To index additional repositories, please upgrade your account or "
                "remove your existing indexed repository.",
            )

    _index_unique_check(app_name, create_git_repo_request.name)

    # Ensure Application exists for the project (auto-create if needed)
    ensure_application_exists(app_name)

    # Get the application (now guaranteed to exist)
    application = Application.get_by_id(app_name)

    _validate_git_credentials(
        user_id=user.id,
        project_name=app_name,
        repo_link=create_git_repo_request.link,
        setting_id=create_git_repo_request.setting_id,
    )

    application.git_repos = [
        GitRepo(**create_git_repo_request.model_dump(exclude={"guardrail_assignments"}), app_id=application.name)
    ]

    index_code_datasource_in_background(
        request_uuid=request_uuid,
        git_repo=application.git_repos[0],
        user=request.state.user,
        background_tasks=tasks,
        guardrail_assignments=create_git_repo_request.guardrail_assignments,
        cron_expression=create_git_repo_request.cron_expression if cron_expression_provided else None,
        timezone=create_git_repo_request.timezone,
    )

    return BaseResponse(
        message=f"Indexing of datasource {create_git_repo_request.name} has been started in the background"
    )


@router.post(
    "/application/{app_name}/index/svn",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(application_access_check)],
    response_model=BaseResponse,
)
def create_svn_index_application(
    app_name: str,
    create_svn_repo_request: CreateSVNIndexRequest,
    request: Request,
    tasks: BackgroundTasks,
    user: User = Depends(authenticate),
):
    request_uuid = request.state.uuid
    request_summary_manager.create_request_summary(
        request_id=request_uuid,
        project_name=app_name,
        user=user.as_user_model(),
    )
    _index_unique_check(app_name, create_svn_repo_request.name)
    ensure_application_exists(app_name)

    SVNIndexService.validate_credentials(
        user_id=user.id,
        project_name=app_name,
        repo_link=create_svn_repo_request.link,
        setting_id=create_svn_repo_request.setting_id,
    )

    svn_repo = SVNRepo(
        **create_svn_repo_request.model_dump(exclude={"guardrail_assignments", "cron_expression"}),
        app_id=app_name,
    )

    def process():
        processor = SVNDatasourceProcessor.create_processor(
            svn_repo=svn_repo,
            user=user,
            request_uuid=request_uuid,
            guardrail_assignments=create_svn_repo_request.guardrail_assignments,
        )
        try:
            processor.process()
        finally:
            # The schedule is user configuration: store it even when indexing fails.
            processor._create_or_update_scheduler(
                create_svn_repo_request.cron_expression, timezone=create_svn_repo_request.timezone
            )

    run_in_background(process, svn_repo.name, tasks)

    return BaseResponse(
        message=f"Indexing of datasource {create_svn_repo_request.name} has been started in the background"
    )


@router.put(
    "/application/{app_name}/index/svn/{repo_name}",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(application_access_check)],
    response_model=BaseResponse,
)
def update_svn_index_application(
    app_name: str,
    repo_name: str,
    tasks: BackgroundTasks,
    request: UpdateIndexRequest,
    raw_request: Request,
    resume_indexing: bool = False,
    skip_reindex: bool = False,
    user: User = Depends(authenticate),
):
    request_uuid = raw_request.state.uuid
    request_summary_manager.create_request_summary(
        request_id=request_uuid,
        project_name=app_name,
        user=user.as_user_model(),
    )
    cron_expression_provided = 'cron_expression' in request.model_fields_set

    index_info = IndexInfo.filter_by_project_and_repo(project_name=app_name, repo_name=repo_name)[0]

    if not Ability(user).can(Action.WRITE, index_info):
        raise_forbidden("update")

    svn_repos = SVNRepo.get_by_app_id(app_id=app_name)
    svn_repo = next((r for r in svn_repos if r.name == repo_name), None)
    if not svn_repo:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=SVN_REPOSITORY_NOT_FOUND_MESSAGE,
            details=f"The SVN repository '{repo_name}' could not be found in project '{app_name}'.",
            help="Please verify the repository name and ensure it exists within the project.",
        )

    if request.name:
        _validate_project_change(request.new_project_name, app_name, repo_name, user)
        _update_svn_index_and_repo_fields(index_info, request, user, svn_repo)

    SVNIndexService.validate_credentials(
        user_id=user.id,
        project_name=index_info.project_name,
        repo_link=svn_repo.link,
        setting_id=svn_repo.setting_id,
    )

    if skip_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, index_info, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=f"{repo_name} updated successfully!")

    def process():
        index = IndexInfo.filter_by_project_and_repo(project_name=app_name, repo_name=repo_name)[0]
        processor = SVNDatasourceProcessor.create_processor(
            svn_repo=svn_repo,
            user=user,
            index=index,
            request_uuid=request_uuid,
            guardrail_assignments=request.guardrail_assignments,
        )
        try:
            if resume_indexing:
                processor.resume()
            else:
                processor.reprocess()
        finally:
            # The schedule is user configuration: store it even when the reindex fails.
            processor._create_or_update_scheduler(
                request.cron_expression if cron_expression_provided else None, timezone=request.timezone
            )

    run_in_background(process, svn_repo.name, tasks)

    if resume_indexing:
        return BaseResponse(message=f"Indexing of datasource {repo_name} has been resumed in the background")

    return BaseResponse(
        message=f"Reindexing of datasource {request.name or repo_name} has been started in the background"
    )


@router.put(
    "/application/{app_name}/index/{repo_name}",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(application_access_check)],
    response_model=BaseResponse,
)
def update_index_application(
    app_name: str,
    repo_name: str,
    tasks: BackgroundTasks,
    request: UpdateIndexRequest,
    raw_request: Request,
    full_reindex: bool = False,
    skip_reindex: bool = False,
    resume_indexing: bool = False,
    user: User = Depends(authenticate),
):
    """
    Endpoint for updating index for application
    By default it will be incremental update (only new commits will be indexed)
    If full_reindex is set to True, then all repository will be reindexed
    """
    request_uuid = raw_request.state.uuid
    request_summary_manager.create_request_summary(
        request_id=request_uuid,
        project_name=app_name,
        user=user.as_user_model(),
    )
    cron_expression_provided = 'cron_expression' in request.model_fields_set

    index_info = IndexInfo.filter_by_project_and_repo(project_name=app_name, repo_name=repo_name)[0]

    if not Ability(user).can(Action.WRITE, index_info):
        raise_forbidden("update")

    try:
        application = Application.get_by_id(app_name)
    except (NotFoundError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=APPLICATION_NOT_FOUND_MESSAGE,
            details=f"The application with name '{app_name}' could not be found in the system.",
            help=APPLICATION_NOT_FOUND_HELP,
        ) from e

    if index_info.repo_type == "svn":
        return _handle_svn_reindex(
            app_name=app_name,
            repo_name=repo_name,
            index_info=index_info,
            request=request,
            request_uuid=request_uuid,
            tasks=tasks,
            user=user,
            full_reindex=full_reindex,
            skip_reindex=skip_reindex,
            resume_indexing=resume_indexing,
            cron_expression_provided=cron_expression_provided,
        )

    repository = _get_repository_with_project_change(application, repo_name, index_info, request)

    if request.name:
        _handle_index_and_repository_update(index_info, request, user, app_name, repo_name, repository)

    _validate_git_credentials(
        user_id=user.id,
        project_name=index_info.project_name,
        repo_link=repository.link,
        setting_id=repository.setting_id,
    )

    if skip_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, index_info, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=f"{repo_name} updated successfully!")

    update_code_datasource_in_background(
        request_uuid=request_uuid,
        git_repo=repository,
        user=user,
        app_name=app_name,
        repo_name=repo_name,
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


@router.get(
    "/application/{app_name}",
    status_code=status.HTTP_200_OK,
    response_model=Application,
    dependencies=[Depends(application_access_check)],
)
def get_application(app_name: str, request: Request):
    try:
        application = Application.get_by_id(app_name)
    except (NotFoundError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=APPLICATION_NOT_FOUND_MESSAGE,
            details=f"The application with name '{app_name}' could not be found in the system.",
            help=APPLICATION_NOT_FOUND_HELP,
        ) from e

    if request.state.user.is_admin:
        index_info = CodeIndexInfo.get_all()
    else:
        index_info = CodeIndexInfo.filter_by_names_or_user(
            project_names=request.state.user.project_names, user=request.state.user
        )

    index_info_repos = [info.repo_name for info in index_info]
    git_repos = GitRepo.get_by_app_id(app_id=application.name)

    git_repos = [repo for repo in git_repos if repo.name in index_info_repos]

    application.git_repos = git_repos

    return application
