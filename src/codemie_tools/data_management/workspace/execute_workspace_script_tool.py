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
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional, Type

from langchain_core.tools import ToolException
from llm_sandbox import SandboxSession
from pydantic import BaseModel, Field, PrivateAttr

from codemie.rest_api.models.agent_workspace import CreateAgentWorkspaceRequest
from codemie.rest_api.security.user import User
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.code_executor_tool import (
    CodeExecutorTool,
)
from codemie_tools.data_management.code_executor.file_export_service import (
    FileExportService,
)
from codemie_tools.data_management.code_executor.local_execution_engine import (
    LocalExecutionEngine,
    _MATPLOTLIB_CAPTURE_SETUP,
)
from codemie_tools.data_management.code_executor.models import ExecutionMode
from codemie_tools.data_management.workspace.tools_vars import (
    EXECUTE_WORKSPACE_SCRIPT_TOOL,
)

logger = logging.getLogger(__name__)


class ExecuteWorkspaceScriptInput(BaseModel):
    script_path: str = Field(description="Workspace-relative path to the Python script to execute.")
    export_files: Optional[list[str]] = Field(
        default=None,
        description="Optional list of workspace-relative files to export from execution results.",
    )


class WorkspaceLocalScriptExecutionEngine(LocalExecutionEngine):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.last_execution_files: list[FileObject] = []

    @staticmethod
    def _validate_relative_path(file_path: str) -> Path:
        relative_path = Path(file_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ToolException(f"Invalid script or file path: {file_path}")
        return relative_path

    @staticmethod
    def _build_script_wrapper(script_path: str) -> str:
        return (
            "import runpy, sys, os\n"
            "sys.path.insert(0, os.getcwd())\n"
            f"runpy.run_path(r'{script_path}', run_name='__main__')\n"
        )

    @staticmethod
    def _hash_content(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _build_input_file_hashes(self, input_files: list[FileObject] | None) -> dict[str, str]:
        input_file_hashes: dict[str, str] = {}

        for file_obj in input_files or []:
            content = file_obj.bytes_content()
            if content is None:
                continue
            relative_path = self._validate_relative_path(file_obj.name).as_posix()
            input_file_hashes[relative_path] = self._hash_content(content)

        return input_file_hashes

    def _collect_changed_files(self, work_dir: str, input_files: list[FileObject] | None) -> list[FileObject]:
        input_file_hashes = self._build_input_file_hashes(input_files)
        work_path = Path(work_dir).resolve()

        for full_path in sorted(work_path.rglob("*")):
            if not full_path.is_file():
                continue

            relative_path = full_path.relative_to(work_path).as_posix()
            if "__pycache__" in full_path.parts or relative_path.endswith(".pyc"):
                continue
            content = full_path.read_bytes()
            if input_file_hashes.get(relative_path) == self._hash_content(content):
                continue
            mime_type, _ = mimetypes.guess_type(relative_path)

            self.last_execution_files.append(
                FileObject(
                    name=relative_path,
                    path=relative_path,
                    mime_type=mime_type or "application/octet-stream",
                    owner=self.user_id,
                    content=content,
                )
            )

        return self.last_execution_files

    def execute_script(
        self,
        script_path: str,
        export_files: list[str] | None,
        input_files: list[FileObject] | None = None,
    ) -> str:
        validated_script_path = self._validate_relative_path(script_path)
        tmp_path = None
        self.last_execution_files = []

        try:
            with tempfile.TemporaryDirectory(delete=True) as work_dir:
                logger.info(f"Script execution temp dir (not deleted): {work_dir}")
                work_path = Path(work_dir).resolve()
                for file_obj in input_files or []:
                    content = file_obj.bytes_content()
                    if content is None:
                        continue
                    safe_name = self._validate_relative_path(file_obj.name)
                    dest = work_path / safe_name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.resolve().is_relative_to(work_path):
                        continue
                    with open(dest, "wb") as file_handle:
                        file_handle.write(content)

                script_full_path = work_path / validated_script_path
                if not script_full_path.exists() or not script_full_path.is_file():
                    raise ToolException(f"Script file '{script_path}' was not found in the execution workspace")

                script_code = script_full_path.read_text(encoding="utf-8", errors="replace")
                self._validate_code_security(script_code)
                code_to_run = self._build_script_wrapper(validated_script_path.as_posix())
                full_code = (
                    _MATPLOTLIB_CAPTURE_SETUP + "\n" + code_to_run if "matplotlib" in script_code else code_to_run
                )

                with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
                    tmp.write(full_code)
                    tmp_path = tmp.name

                result = subprocess.run(
                    [os.sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=self.config.execution_timeout,
                    cwd=work_dir,
                )

                if result.returncode != 0:
                    raise ToolException(f"Code execution failed:\n{result.stderr}")

                output_text = result.stdout.strip() if result.stdout else "Code executed successfully with no output."
                if result.stderr and result.stderr.strip():
                    output_text += f"\n\nSTDERR:\n{result.stderr.strip()}"
                self._collect_changed_files(work_dir, input_files)

                exported_urls: list[str] | None = None
                if export_files:
                    exported_urls = self._export_created_files(export_files, work_dir)
                if exported_urls:
                    output_text += "\n\nExported files:\n" + "\n".join(f"- {url}" for url in exported_urls)

                return output_text
        except subprocess.TimeoutExpired:
            raise ToolException(
                f"Code execution timed out after {self.config.execution_timeout} seconds. "
                "This may indicate an infinite loop or a resource-intensive operation."
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


class WorkspaceScriptRunner(CodeExecutorTool):
    last_execution_files: list[FileObject] = Field(default_factory=list, exclude=True)

    def execute_script(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
        self.last_execution_files = []
        validated_script_path = self._validate_script_path(script_path)

        if self.config.execution_mode == ExecutionMode.LOCAL:
            return self._execute_local_script(validated_script_path, export_files)

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
        return "import runpy\n" f"runpy.run_path(r'{script_path}', run_name='__main__')\n"

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
        self, session: SandboxSession, workdir: str, input_file_hashes: dict[str, str]
    ) -> list[FileObject]:
        current_snapshot = self._get_sandbox_file_snapshot(session, workdir)
        changed_paths = [
            file_path
            for file_path, content_hash in sorted(current_snapshot.items())
            if input_file_hashes.get(file_path) != content_hash
        ]

        if not changed_paths:
            return []

        export_service = FileExportService(self.file_repository, self.user_id)
        return export_service.collect_files_from_execution(session, changed_paths, workdir)

    def _get_sandbox_file_snapshot(self, session: SandboxSession, workdir: str) -> dict[str, str]:
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
        session, session_time = self._acquire_session(user_workdir)
        input_file_hashes = self._get_input_file_hashes()

        if self.input_files:
            self._upload_files_to_sandbox(session, self.input_files, user_workdir)

        script_code = self._get_script_content(script_path)
        self._validate_code_security(session, script_code)
        wrapper_code = self._build_script_wrapper(script_path)

        result, exec_time = self._execute_code_sandbox(session, wrapper_code)
        self._log_execution_timing(session_time, exec_time)

        result_text = self._format_execution_result(result)
        self.last_execution_files = self._collect_sandbox_changed_files(session, user_workdir, input_file_hashes)
        exported_files = self._export_files_from_execution(session, export_files, user_workdir)
        if exported_files:
            result_text += ", ".join(exported_files)

        return result_text

    def _execute_local_script(self, script_path: str, export_files: Optional[list[str]] = None) -> str:
        engine = WorkspaceLocalScriptExecutionEngine(
            self.config, self.file_repository, self.user_id, self.security_policy
        )
        result = engine.execute_script(
            script_path,
            export_files=export_files,
            input_files=self.input_files,
        )
        self.last_execution_files = engine.last_execution_files
        return result


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
    _workspace_id: str | None = PrivateAttr(default=None)

    def _get_workspace_id(self) -> str:
        if self._workspace_id is None:
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
