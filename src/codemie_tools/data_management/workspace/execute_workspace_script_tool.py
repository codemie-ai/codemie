# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Optional, Type

from langchain_core.tools import ToolException
from pydantic import BaseModel, Field, PrivateAttr

from codemie.rest_api.models.agent_workspace import CreateAgentWorkspaceRequest
from codemie.rest_api.security.user import User
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_object import FileObject

import time as _time

from codemie_tools.data_management.code_executor.batch_job_runner import BatchJobRunner
from codemie_tools.data_management.code_executor.code_executor_tool import (
    CodeExecutorTool,
)
from codemie_tools.data_management.code_executor.file_export_service import (
    FileExportService,
)
from codemie_tools.data_management.code_executor.llm_sandbox import is_sandbox_system_file_path
from codemie_tools.data_management.code_executor.models import ExecutionMode, SandboxMode
from codemie_tools.data_management.workspace.tools_vars import (
    EXECUTE_WORKSPACE_SCRIPT_TOOL,
)

logger = logging.getLogger(__name__)


def _is_system_output_path(file_path: str) -> bool:
    return file_path.endswith(".pyc") or is_sandbox_system_file_path(file_path)


class ExecuteWorkspaceScriptInput(BaseModel):
    script_path: str = Field(description="Workspace-relative path to the Python script to execute.")
    export_files: Optional[list[str]] = Field(
        default=None,
        description="Optional list of workspace-relative files to export from execution results.",
    )


class WorkspaceScriptRunner(CodeExecutorTool):
    conversation_id: str | None = Field(default=None, exclude=True)
    last_execution_files: list[FileObject] = Field(default_factory=list, exclude=True)

    def __init__(
        self,
        file_repository,
        user_id: str | None = "",
        input_files: list[FileObject] | None = None,
        execution_mode: ExecutionMode | None = None,
        conversation_id: str | None = None,
    ):
        super().__init__(
            file_repository=file_repository,
            user_id=user_id,
            input_files=input_files,
            execution_mode=execution_mode,
        )
        self.conversation_id = conversation_id

    def _get_user_workdir(self) -> str:
        base_workdir = super()._get_user_workdir()
        if not self.conversation_id:
            return base_workdir

        safe_conversation_id = self.conversation_id.replace("/", "_").replace("\\", "_")
        return f"{base_workdir}/{safe_conversation_id}"

    def execute_script(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
        self.last_execution_files = []
        validated_script_path = self._validate_script_path(script_path)

        return self._execute_sandbox_script(validated_script_path, export_files)

    @staticmethod
    def _validate_script_path(script_path: str) -> str:
        normalized_script_path = Path(script_path)
        if normalized_script_path.is_absolute() or ".." in normalized_script_path.parts:
            raise ToolException(f"Invalid script_path: {script_path}")

        normalized = normalized_script_path.as_posix()
        if normalized in {"", "."}:
            raise ToolException("script_path must point to a file")
        return normalized

    @staticmethod
    def _build_script_wrapper(script_path: str) -> str:
        return f"import runpy\nrunpy.run_path(r'{script_path}', run_name='__main__')\n"

    @staticmethod
    def _hash_content(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _get_input_file_hashes(self) -> dict[str, str]:
        input_file_hashes: dict[str, str] = {}

        for file_obj in self.input_files or []:
            content = file_obj.bytes_content()
            if content is None:
                continue
            input_file_hashes[Path(file_obj.name).as_posix()] = self._hash_content(content)

        return input_file_hashes

    def _get_script_content(self, script_path: str) -> str:
        for file_obj in self.input_files:
            if Path(file_obj.name).as_posix() == script_path:
                content = file_obj.string_content()
                if content is None:
                    raise ToolException(f"Script file '{script_path}' has no content")
                return content

        raise ToolException(f"Script file '{script_path}' was not provided to the execution workspace")

    def _collect_sandbox_changed_files(
        self, session: Any, workdir: str, input_file_hashes: dict[str, str]
    ) -> list[FileObject]:
        current_snapshot = self._get_sandbox_file_snapshot(session, workdir)
        changed_paths = [
            file_path
            for file_path, content_hash in sorted(current_snapshot.items())
            if input_file_hashes.get(file_path) != content_hash and not _is_system_output_path(file_path)
        ]

        if not changed_paths:
            return []

        export_service = FileExportService(self.file_repository, self.user_id)
        return export_service.collect_files_from_execution(session, changed_paths, workdir)

    def _get_sandbox_file_snapshot(self, session: Any, workdir: str) -> dict[str, str]:
        snapshot_code = (
            "import hashlib, json, os\n"
            f"root = {workdir!r}\n"
            "snapshot = {}\n"
            "for current_root, _, files in os.walk(root):\n"
            "    if '__pycache__' in current_root.split(os.sep):\n"
            "        continue\n"
            "    for name in files:\n"
            "        path = os.path.join(current_root, name)\n"
            "        rel = os.path.relpath(path, root).replace(os.sep, '/')\n"
            "        if rel.endswith('.pyc'):\n"
            "            continue\n"
            "        with open(path, 'rb') as file_handle:\n"
            "            snapshot[rel] = hashlib.sha256(file_handle.read()).hexdigest()\n"
            "print('__CODEMIE_FILE_SNAPSHOT__' + json.dumps(snapshot, sort_keys=True))\n"
        )
        result = session.run(snapshot_code, timeout=self.config.execution_timeout)
        if result.exit_code != 0:
            raise ToolException(f"Failed to inspect sandbox files.\n\n{self._format_execution_result(result)}")

        stdout = result.stdout or ""
        for line in reversed(stdout.splitlines()):
            if line.startswith("__CODEMIE_FILE_SNAPSHOT__"):
                payload = line.removeprefix("__CODEMIE_FILE_SNAPSHOT__")
                return json.loads(payload)

        return {}

    def _execute_sandbox_script(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
        user_workdir = self._get_user_workdir()
        self._validate_export_paths(export_files, user_workdir)

        if self.config.sandbox_mode == SandboxMode.JOBS:
            return self._execute_sandbox_script_jobs(script_path, export_files, user_workdir)

        with self._sandbox_session(user_workdir) as session:
            input_file_hashes = self._get_input_file_hashes()

            if self.input_files:
                self._upload_files_to_sandbox(session, self.input_files, user_workdir)

            script_code = self._get_script_content(script_path)
            self._validate_code_security(session, script_code)
            wrapper_code = self._build_script_wrapper(script_path)

            result, exec_time = self._execute_code_sandbox(session, wrapper_code)
            self._log_execution_timing(0.0, exec_time)

            result_text = self._format_execution_result(result)
            self.last_execution_files = self._collect_sandbox_changed_files(session, user_workdir, input_file_hashes)
            exported_files = self._export_files_from_execution(session, export_files, user_workdir)
            if exported_files:
                result_text += ", ".join(exported_files)

            return result_text

    def _execute_sandbox_script_jobs(
        self, script_path: str, export_files: Optional[list[str]], user_workdir: str
    ) -> str:
        script_code = self._get_script_content(script_path)
        self._validate_code_security_policy(script_code)
        wrapper_code = self._build_script_wrapper(script_path)

        input_file_hashes = self._get_input_file_hashes()
        input_bytes = self._read_input_file_bytes(self.input_files)

        start = _time.time()
        result = BatchJobRunner(self.config).run(
            wrapper_code,
            input_files=input_bytes,
            export_files=export_files,
            workdir=user_workdir,
            baseline_hashes=input_file_hashes,
        )
        self._log_execution_timing(0.0, _time.time() - start)

        self.last_execution_files = [
            FileObject(
                name=rel_path,
                path=rel_path,
                mime_type=mimetypes.guess_type(rel_path)[0] or "application/octet-stream",
                owner=self.user_id,
                content=content,
            )
            for rel_path, content in result.changed_files.items()
        ]

        text = self._format_execution_result(result)
        urls = self._store_exported_bytes(result.exported_files)
        if urls:
            text += ", ".join(urls)
        return text


def _default_workspace_service() -> Any:
    from codemie.service.agent_workspace_service import AgentWorkspaceService

    return AgentWorkspaceService()


class ExecuteWorkspaceScriptTool(CodeMieTool):
    name: str = EXECUTE_WORKSPACE_SCRIPT_TOOL.name
    description: str = EXECUTE_WORKSPACE_SCRIPT_TOOL.description
    args_schema: Type[BaseModel] = ExecuteWorkspaceScriptInput
    conversation_id: str = Field(exclude=True)
    user: User = Field(exclude=True)
    workspace_service: Any = Field(default_factory=_default_workspace_service, exclude=True)
    workspace_id: str | None = Field(default=None, exclude=True)
    _workspace_id: str | None = PrivateAttr(default=None)

    def _get_workspace_id(self) -> str:
        if self._workspace_id is None:
            if self.workspace_id:
                self.workspace_service.get_workspace(self.workspace_id, self.user)
                self._workspace_id = self.workspace_id
            else:
                workspace = self.workspace_service.create_workspace(
                    CreateAgentWorkspaceRequest(conversation_id=self.conversation_id),
                    self.user,
                )
                self._workspace_id = workspace.id
        return self._workspace_id

    @staticmethod
    def _dump_json(payload) -> str:
        if isinstance(payload, list):
            data = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in payload]
        elif hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json")
        else:
            data = payload
        return json.dumps(data, ensure_ascii=False, indent=2)

    def execute(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
        workspace_id = self._get_workspace_id()
        response = self.workspace_service.execute_workspace_script(
            workspace_id=workspace_id,
            script_path=script_path,
            user=self.user,
            export_files=export_files,
        )
        return self._dump_json(response)
