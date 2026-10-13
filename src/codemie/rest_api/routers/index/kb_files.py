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


from fastapi import APIRouter, BackgroundTasks, status, Depends
from starlette.requests import Request

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.datasource.file.file_datasource_processor import FileDatasourceProcessor
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.index import (
    IndexKnowledgeBaseFileRequest,
    UpdateKnowledgeBaseFileRequest,
)

from codemie.service.datasource.file_datasource_service import FileDatasourceService
from codemie.service.datasource.zip_utils import ZipExtractionError
from codemie.use_cases.datasource.update_file_datasource_use_case import UpdateFileDatasourceUseCase
from codemie.rest_api.routers.index._common import (
    _index_unique_check,
    _kb_demo_user_check,
)


router = APIRouter()


@router.put("/index/knowledge_base/file", status_code=status.HTTP_200_OK)
def update_knowledge_base_files(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    request: UpdateKnowledgeBaseFileRequest = Depends(),
):
    try:
        return UpdateFileDatasourceUseCase(FileDatasourceService()).execute(
            request=request,
            user=raw_request.state.user,
            background_tasks=background_tasks,
            request_uuid=raw_request.state.uuid,
        )
    except ZipExtractionError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=str(exc),
            details=exc.detail,
            help=exc.help_text,
        ) from exc


@router.post("/index/knowledge_base/file", status_code=status.HTTP_200_OK)
def index_knowledge_base_files(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    request: IndexKnowledgeBaseFileRequest = Depends(),
):
    _index_unique_check(request.project_name, request.name)

    _kb_demo_user_check(raw_request.state.user)

    parsed_guardrail_assignments = FileDatasourceService.parse_guardrail_assignments(request.guardrail_assignments)

    file_repo = FileRepositoryFactory.get_current_repository()

    try:
        files_paths, uploaded_files = FileDatasourceService.process_files_batch(
            request.files, raw_request.state.user.id, file_repo
        )
    except ZipExtractionError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=str(exc),
            details=exc.detail,
            help=exc.help_text,
        ) from exc

    file_data_source_processor = FileDatasourceProcessor(
        datasource_name=request.name,
        project_name=request.project_name,
        files_paths=files_paths,
        uploaded_files=uploaded_files,
        description=request.description,
        project_space_visible=request.project_space_visible,
        csv_separator=request.csv_separator,
        csv_start_row=request.csv_start_row,
        csv_rows_per_document=request.csv_rows_per_document,
        request_uuid=raw_request.state.uuid,
        user=raw_request.state.user,
        embedding_model=request.embedding_model,
        guardrail_assignments=parsed_guardrail_assignments,
        include_email_attachments=request.include_email_attachments,
    )

    file_data_source_processor.schedule(background_tasks)
    return BaseResponse(message=file_data_source_processor.started_message)
