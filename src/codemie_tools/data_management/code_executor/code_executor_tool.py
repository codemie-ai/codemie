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

import logging
import time
from pathlib import Path
from typing import Type, Optional, Any, List, Tuple

from codemie.repository.base_file_repository import FileRepository
from langchain_core.tools import ToolException
from llm_sandbox import SandboxSession
from llm_sandbox.exceptions import SandboxTimeoutError
from llm_sandbox.security import SecurityPolicy
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.file_export_service import FileExportService
from codemie_tools.data_management.code_executor.file_upload_service import FileUploadService
from codemie_tools.data_management.code_executor.llm_sandbox import apply_llm_sandbox_patch
from codemie_tools.data_management.code_executor.local_execution_engine import LocalExecutionEngine
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig, ExecutionMode
from codemie_tools.data_management.code_executor.security_policies import (
    get_codemie_security_policy,
    get_restricted_module_names,
)
from codemie_tools.data_management.code_executor.session_manager import SandboxSessionManager
from codemie_tools.data_management.code_executor.tools_vars import (
    CODE_EXECUTOR_TOOL,
    COMMON_SANDBOX_LIBRARIES,
    COMMON_SANDBOX_SYSTEM_TOOLS,
    SAFE_STDLIB_MODULES,
)

logger = logging.getLogger(__name__)

# Apply Kubernetes performance patch on module load
# This fixes the slow file upload issue in llm-sandbox (1s delay per 4KB chunk)
try:
    apply_llm_sandbox_patch()
except ImportError:
    logger.debug("Kubernetes support not available, patch not applied")


def get_code_executor_input_schema(
    execution_mode: ExecutionMode,
    blocked_modules: Optional[str] = None,
    file_names: Optional[List[str]] = None,
) -> Type[BaseModel]:
    """
    Create input schema for code execution.

    For local mode: Returns PythonRunCodeInput with python_script field
    For sandbox mode: Returns CodeExecutorInput with code field and security info

    Args:
        execution_mode: Execution mode (LOCAL or SANDBOX)
        blocked_modules: Comma-separated string of blocked modules (for sandbox mode)
        file_names: Optional list of filenames to include in description

    Returns:
        BaseModel class with customized field descriptions
    """

    # Base code description
    code_description = f"""
        Python code to execute in an isolated environment.

        IMPORTANT CONSTRAINTS:
        - Code MUST be Python only
        - ONLY use pre-installed libraries or Python standard library modules
        - External libraries NOT in the pre-installed list are NOT available and will cause import errors
        - Code that attempts to import unavailable libraries will FAIL

        Pre-installed Python libraries: {', '.join(COMMON_SANDBOX_LIBRARIES)}

        Available system tools (can be invoked via subprocess if allowed): {', '.join(COMMON_SANDBOX_SYSTEM_TOOLS)}

        SAFE standard library modules: {SAFE_STDLIB_MODULES}

        BLOCKED modules for security: {blocked_modules}

        MATPLOTLIB PLOT GENERATION - TWO APPROACHES:

        Approach 1 (RECOMMENDED for filename control):
          plt.figure(figsize=(12, 6))
          plt.plot(x, y, label="My Data")
          plt.xlabel("X axis")
          plt.ylabel("Y axis")
          plt.title("My Plot Title")
          plt.legend()
          plt.grid(True)
          plt.savefig("my_plot.png")
          # Then specify export_files=["my_plot.png"] parameter

        Approach 2 (automatic capture):
          plt.figure(figsize=(12, 6))
          plt.plot(x, y, label="My Data")
          plt.xlabel("X axis")
          plt.ylabel("Y axis")
          plt.title("My Plot Title")
          plt.legend()
          plt.grid(True)
          plt.show()  # Auto-captured with auto-generated filename
        """.strip()

    # Add file information if provided
    if file_names:
        file_list = ", ".join([f"'{name}'" for name in file_names])
        files_info = (
            f"AVAILABLE FILES IN WORKING DIRECTORY:\n{file_list}\n"
            f"IMPORTANT: Use these EXACT filenames (including brackets, spaces, parentheses) in your code.\n\n"
        )
        code_description = f"{files_info}{code_description}"

    class CodeExecutorInput(BaseModel):
        code: str = Field(description=code_description)
        export_files: Optional[List[str]] = Field(
            default=None,
            description="List of file paths to export after code execution. Files will be stored using file_repository.",
        )

    return CodeExecutorInput


class CodeExecutorTool(CodeMieTool):
    """
    Unified tool for executing Python code with two execution modes.

    Execution Modes:
    - sandbox (default): Isolated Kubernetes pod execution with production-grade security
    - local: Embedded kernel execution for development environments

    Sandbox Mode Features:
    - Infinite Loop Protection: Automatic timeout after execution_timeout seconds
    - Resource-Intensive Operation Control: CPU and memory limits via pod manifest
    - Session Lifetime Management: Sessions expire after session_timeout seconds
    - Security Policy: Code validation before execution using production-grade policy
    - Production-grade security optimized for shared multi-tenant environments
    - File upload/export capabilities with isolation

    Local Mode Features:
    - Lightweight embedded kernel execution
    - Suitable for development and testing
    - Basic timeout protection
    - File creation support with file repository integration
    - WARNING: Limited security controls - not recommended for production

    Configuration:
    Set execution_mode via CODE_EXECUTOR_EXECUTION_MODE environment variable:
    - "sandbox" (default) - Use Kubernetes-based isolated execution
    - "local" - Use embedded kernel execution
    """

    name: str = CODE_EXECUTOR_TOOL.name
    description: str = CODE_EXECUTOR_TOOL.description
    args_schema: Type[BaseModel] = get_code_executor_input_schema(
        execution_mode=ExecutionMode.LOCAL,
        blocked_modules=None,
        file_names=None,
    )
    config: Optional[CodeExecutorConfig] = None
    file_repository: Optional[Any] = None
    user_id: Optional[str] = ""
    input_files: Optional[List[FileObject]] = Field(default=None, exclude=True)
    security_policy: SecurityPolicy = None
    _custom_pod_manifest: Optional[dict] = None

    def __init__(
        self,
        file_repository: FileRepository,
        user_id: Optional[str] = "",
        input_files: Optional[List[FileObject]] = None,
        execution_mode: Optional[ExecutionMode] = None,
    ):
        """
        Initialize the CodeExecutorTool.

        Configuration can be provided directly or loaded from environment variables.
        Environment variables:
        - CODE_EXECUTOR_* for various configuration options (see CodeExecutorConfig)
        - CODE_EXECUTOR_KUBECONFIG_PATH: Path to kubeconfig file (takes priority over in-cluster config)

        Execution mode precedence (highest to lowest):
        1. execution_mode parameter (if provided)
        2. CODE_EXECUTOR_EXECUTION_MODE env var (default: local)

        Args:
            file_repository: Repository for storing files generated by code execution
            user_id: User ID for file ownership attribution
            input_files: Optional list of FileObject instances to upload to sandbox before execution
            execution_mode: Execution mode (ExecutionMode.LOCAL or ExecutionMode.SANDBOX).
                           If provided, takes highest precedence over environment configuration.
        """
        super().__init__()
        base_config = CodeExecutorConfig.from_env()

        # Determine execution mode with precedence:
        # 1. Explicit execution_mode parameter (highest priority)
        # 2. Config from CODE_EXECUTOR_EXECUTION_MODE env var (default: local)
        self._mode_override = execution_mode is not None
        if execution_mode:
            self.config = base_config.model_copy(update={"execution_mode": execution_mode})
        else:
            self.config = base_config

        self.file_repository = file_repository
        self.user_id = user_id
        self.input_files = input_files or []

        if self.input_files:
            logger.debug(f"Input files: {len(self.input_files)}")

        # Initialize security policy and blocked modules
        yaml_path = Path(self.config.yaml_policy_path) if self.config.yaml_policy_path else None
        blocked_modules_str = None

        #
        self.security_policy = get_codemie_security_policy(
            severity_threshold=self.config.security_threshold, yaml_config_path=yaml_path
        )
        blocked_modules_list = get_restricted_module_names(
            severity_threshold=self.config.security_threshold, yaml_config_path=yaml_path
        )
        blocked_modules_str = ", ".join(blocked_modules_list) if blocked_modules_list else "None (unrestricted mode)"

        # Create args_schema for both modes
        file_names = [f.name for f in self.input_files] if self.input_files else None
        self.args_schema = get_code_executor_input_schema(
            execution_mode=self.config.execution_mode,
            blocked_modules=blocked_modules_str,
            file_names=file_names,
        )

    def _get_user_workdir(self) -> str:
        """
        Get user-specific working directory to ensure isolation between users.

        Uses sanitized user ID to create isolated workdir paths, preventing
        directory traversal attacks.

        Returns:
            str: User-specific workdir path
        """
        if self.user_id:
            safe_user_id = self.user_id.replace("/", "_").replace("\\", "_")
            return f"{self.config.workdir_base}/{safe_user_id}"
        return self.config.workdir_base

    def _get_available_pod_name(self) -> Optional[str]:
        """
        DEPRECATED: Do not use this method directly!

        This method is deprecated because calling it bypasses the session_manager's
        global lock, which causes race conditions and over-provisioning when multiple
        threads start simultaneously.

        Instead, always call session_manager.get_session(pod_name=None, ...) and let
        the session_manager make ALL pod selection decisions within its global lock.

        This method is kept for backward compatibility but should not be used.

        Returns:
            None: Always returns None to force proper locking in session_manager
        """
        logger.warning(
            "_get_available_pod_name() is deprecated and should not be called directly. "
            "Use session_manager.get_session(pod_name=None, ...) instead."
        )
        return None  # Always return None to force session_manager to make the decision

    def _create_default_pod_manifest(self, pod_name: str) -> dict:
        """
        Create a default pod manifest with appropriate resource limits and security settings.

        Resource-Intensive Operation Control:
        - Memory and CPU limits configured via CodeExecutorConfig
        - Prevents resource exhaustion in multi-tenant environment

        Security Features:
        - Strict security policies to prevent system command execution
        - No privilege escalation allowed
        - All capabilities dropped
        - Seccomp profile to restrict system calls
        - No host namespace access

        Pod is shared between users with isolation at the workdir level.
        Each pod has a single container. The pool manages multiple pods for load distribution.

        Args:
            pod_name: Fixed pod name from the pool for reuse

        Returns:
            dict: Pod manifest configuration with resource limits and security settings
        """
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self.config.namespace,
                "labels": {"app": "codemie-executor", "component": "code-executor"},
            },
            "spec": {
                "containers": [
                    {
                        "name": "python-executor",
                        "image": self.config.docker_image,
                        "tty": True,
                        "stdin": True,
                        "securityContext": {
                            "runAsUser": self.config.run_as_user,
                            "runAsGroup": self.config.run_as_group,
                            "runAsNonRoot": True,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                            "privileged": False,
                            "readOnlyRootFilesystem": False,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "volumeMounts": [
                            {"name": "tmp", "mountPath": "/tmp/runtime"},  # NOSONAR
                            {"name": "workdir", "mountPath": "/home/codemie"},
                        ],
                        "resources": {
                            "limits": {
                                "memory": self.config.memory_limit,
                                "cpu": self.config.cpu_limit,
                            },
                            "requests": {
                                "memory": self.config.memory_request,
                                "cpu": self.config.cpu_request,
                            },
                        },
                    }
                ],
                "volumes": [{"name": "tmp", "emptyDir": {}}, {"name": "workdir", "emptyDir": {}}],
                "securityContext": {
                    "runAsUser": self.config.run_as_user,
                    "runAsGroup": self.config.run_as_group,
                    "fsGroup": self.config.fs_group,
                    "runAsNonRoot": True,
                    "seccompProfile": {"type": "RuntimeDefault"},
                    "supplementalGroups": [],
                    "fsGroupChangePolicy": "OnRootMismatch",
                },
                "hostNetwork": False,
                "hostPID": False,
                "hostIPC": False,
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
            },
        }

    def execute(self, code: str, export_files: Optional[List[str]] = None) -> str:
        """
        Execute Python code using the configured execution mode.

        Routes execution to either sandbox (Kubernetes) or local (embedded kernel)
        based on the execution_mode configuration.

        Sandbox Mode Workflow:
        1. Acquires a sandbox session (reuses existing or creates new)
        2. Uploads input files to sandbox if provided (from constructor)
        3. Validates code against security policy
        4. Executes code with timeout protection
        5. Processes and formats results
        6. Exports files if requested

        Local Mode Workflow:
        1. Creates temporary directory for kernel
        2. Starts embedded kernel
        3. Executes code with timeout protection
        6. Exports files if requested
        5. Returns formatted results

        Args:
            code: The Python code to execute
            export_files: List of file paths to export after execution (sandbox mode only)

        Returns:
            Execution result including stdout, stderr, and exit code.
            For sandbox mode with export_files and file_repository,
            includes URLs for exported files.

        Raises:
            ToolException: If execution fails, security validation fails,
                          session acquisition fails, or file upload fails
        """
        # Log execution mode and reason
        if self.config.execution_mode == ExecutionMode.LOCAL:
            logger.debug(
                f"Executing code in LOCAL mode (reason: "
                f"{'explicit parameter' if hasattr(self, '_mode_override') else 'CODE_EXECUTOR_EXECUTION_MODE env var or default'})"
            )
            return self._execute_local(code, export_files)
        else:
            logger.debug(
                f"Executing code in SANDBOX mode (reason: "
                f"{'explicit parameter' if hasattr(self, '_mode_override') else 'CODE_EXECUTOR_EXECUTION_MODE env var or default'})"
            )
            return self._execute_sandbox(code, export_files)

    def _execute_sandbox(self, code: str, export_files: Optional[List[str]] = None) -> str:
        """
        Execute Python code in isolated Kubernetes sandbox environment.

        Args:
            code: The Python code to execute
            export_files: List of file paths to export after execution

        Returns:
            Execution result including stdout, stderr, and exit code

        Raises:
            ToolException: If execution fails
        """
        try:
            user_workdir = self._get_user_workdir()
            session, session_time = self._acquire_session(user_workdir)

            # Upload input files to sandbox if provided in constructor
            if self.input_files:
                self._upload_files_to_sandbox(session, self.input_files, user_workdir)

            self._validate_code_security(session, code)

            result, exec_time = self._execute_code_sandbox(session, code)
            self._log_execution_timing(session_time, exec_time)

            result_text = self._format_execution_result(result)
            exported_files = self._export_files_from_execution(session, export_files, user_workdir)
            if exported_files:
                result_text += ", ".join(exported_files)

            return result_text

        except ImportError as e:
            raise ToolException(
                "Required library is not installed. Please install it with: pip install 'llm-sandbox[k8s]'"
            ) from e
        except ToolException:
            raise
        except Exception as e:
            # Enhanced error logging with exception type and details
            error_type = type(e).__name__
            error_msg = str(e)
            error_repr = repr(e)

            logger.error(
                f"Error executing code - Exception Type: {error_type}, Message: {error_msg}, Repr: {error_repr}",
                exc_info=True,
            )

            # Provide detailed error message to user
            detailed_error = f"{error_type}: {error_msg}" if error_msg else f"{error_type}: {error_repr}"
            raise ToolException(f"Error executing code: {detailed_error}") from e

    def _execute_local(self, code: str, export_files: list[str] | None) -> str:
        """
        Execute Python code in local subprocess via LocalExecutionEngine.

        WARNING: This mode has limited security controls and is not suitable
        for production or multi-tenant environments.

        Args:
            code: The Python code to execute

        Returns:
            Execution result as formatted string

        Raises:
            ToolException: If execution fails or times out
        """
        engine = LocalExecutionEngine(self.config, self.file_repository, self.user_id)
        return engine.execute(code, export_files=export_files, input_files=self.input_files)

    def _upload_files_to_sandbox(self, session: SandboxSession, file_objects: List[FileObject], workdir: str) -> None:
        """
        Upload files from file repository to the sandbox environment.

        Files are uploaded to the user's working directory in the sandbox,
        making them available for code execution by their original filenames.

        Args:
            session: Active sandbox session
            file_objects: List of FileObject instances to upload
            workdir: Working directory in the sandbox

        Raises:
            ToolException: If file upload fails or file repository is not available
        """
        upload_service = FileUploadService(self.file_repository)
        upload_service.upload_files_to_sandbox(session, file_objects, workdir)

    def _acquire_session(self, user_workdir: str) -> Tuple[SandboxSession, float]:
        """
        Acquire a sandbox session for code execution.

        Args:
            user_workdir: User-specific working directory

        Returns:
            tuple: (session, elapsed_time_seconds)

        Raises:
            ToolException: If session acquisition fails
        """
        start_time = time.time()

        # IMPORTANT: Do NOT call _get_available_pod_name() here!
        # Let session_manager.get_session() make ALL pod selection decisions
        # within its global lock to prevent race conditions.
        # Calling _get_available_pod_name() here would bypass the lock and
        # cause over-provisioning when multiple threads start simultaneously.

        session_manager = SandboxSessionManager(config=self.config)
        # Create manifest with placeholder name (llm_sandbox will generate actual name)
        pod_manifest = self._custom_pod_manifest or self._create_default_pod_manifest("codemie-executor-new")

        logger.debug(f"Requesting session for workdir: {user_workdir}")
        session = session_manager.get_session(
            pod_name=None,  # Let session_manager decide which pod to use
            workdir=user_workdir,
            pod_manifest=pod_manifest,
            security_policy=self.security_policy,
        )

        elapsed = time.time() - start_time
        logger.debug(f"Session ready in {elapsed:.2f}s")

        return session, elapsed

    @staticmethod
    def _validate_code_security(session, code: str) -> None:
        """
        Validate code against security policy before execution.

        Args:
            session: Active sandbox session
            code: Python code to validate

        Raises:
            ToolException: If code fails security validation
        """
        is_safe, violations = session.is_safe(code)

        if not is_safe:
            violation_details = [f"  • [{v.severity.name}] {v.description}" for v in violations]
            error_msg = (
                f"Code failed security validation ({len(violations)} violation(s) detected):\n"
                + "\n".join(violation_details)
                + "\n\nPlease review your code and remove any restricted operations."
            )
            logger.warning(
                f"Security validation failed: {len(violations)} violation(s) - {', '.join([v.description for v in violations[:3]])}"
            )
            raise ToolException(error_msg)

    def _execute_code_sandbox(self, session, code: str) -> Tuple[Any, float]:
        """
        Execute code in the sandbox with timeout protection and per-pod locking.

        Uses per-pod locking to ensure thread-safe execution when multiple threads
        share the same sandbox session. This prevents WebSocket connection corruption
        and ensures serial execution of code in the same pod.

        Args:
            session: Active sandbox session
            code: Python code to execute

        Returns:
            tuple: (execution_result, elapsed_time_seconds)

        Raises:
            ToolException: If execution times out
        """
        start_time = time.time()

        # Log code summary for debugging
        code_lines = code.strip().split("\n")
        code_summary = f"{len(code_lines)} lines, {len(code)} chars"
        first_line = code_lines[0][:60] + "..." if len(code_lines[0]) > 60 else code_lines[0]

        # Get pod name from session attribute (set by session manager)
        pod_name = getattr(session, "_codemie_pod_name", None)

        # Get per-pod lock for thread-safe execution
        # Multiple threads can use the same session, but only one can execute at a time
        from codemie_tools.data_management.code_executor.session_manager import (
            SandboxSessionManager,
        )

        session_manager = SandboxSessionManager(config=self.config)
        lock = session_manager._get_or_create_lock(pod_name or "unknown_pod")

        try:
            with lock:
                result = session.run(code, timeout=self.config.execution_timeout)
        except SandboxTimeoutError as e:
            error_msg = (
                f"Code execution timed out after {self.config.execution_timeout} seconds. "
                "This may indicate an infinite loop or a resource-intensive operation. "
                "Please review your code and consider optimizing it."
            )
            logger.error(error_msg)
            raise ToolException(error_msg) from e

        elapsed = time.time() - start_time
        logger.debug(
            f"Executing code ({code_summary}): {first_line} - executed in {elapsed:.2f}s (exit_code={result.exit_code})"
        )

        return result, elapsed

    @staticmethod
    def _log_execution_timing(session_time: float, exec_time: float) -> None:
        """
        Log execution timing information.

        Args:
            session_time: Time spent acquiring session
            exec_time: Time spent executing code
        """
        total_time = session_time + exec_time
        logger.debug(
            f"Total execution time: session={session_time:.2f}s, exec={exec_time:.2f}s, total={total_time:.2f}s"
        )

    def _format_execution_result(self, result: Any) -> str:
        """
        Format execution result into a human-readable string.

        Filters out internal setup messages from stdout to provide cleaner output.

        Args:
            result: Execution result object with stdout, stderr, and exit_code

        Returns:
            str: Formatted result string

        Raises:
            ToolException: If execution failed (non-zero exit code)
        """
        output_parts = []

        # Filter stdout to remove internal setup messages
        if result.stdout:
            filtered_stdout = self._filter_stdout(result.stdout)
            if filtered_stdout:
                output_parts.append(f"{filtered_stdout}")

        if result.stderr:
            output_parts.append(f"STDERR:\n{result.stderr}")

        if result.exit_code != 0:
            logger.warning(f"Code execution failed with exit code {result.exit_code}")
            raise ToolException(f"Code execution failed.\n\n{chr(10).join(output_parts)}")

        return chr(10).join(output_parts) if output_parts else "Code executed successfully with no output."

    @staticmethod
    def _filter_stdout(stdout: str) -> str:
        """
        Filter out internal setup messages from stdout.

        Args:
            stdout: Raw stdout output

        Returns:
            str: Filtered stdout with internal messages removed
        """
        # Filter out setup/initialization messages
        lines = stdout.split("\n")
        filtered_lines = [line for line in lines if "Python plot detection setup complete" not in line]
        return "\n".join(filtered_lines).strip()

    def _export_files_from_execution(self, session, file_paths: Optional[List[str]], workdir: str) -> List[str]:
        """
        Export files from the execution environment and store them using file_repository.

        Args:
            session: The active execution session
            file_paths: List of paths to export from the execution environment
            workdir: The user-specific working directory

        Returns:
            List of URLs for the stored files
        """
        export_service = FileExportService(self.file_repository, self.user_id)
        return export_service.export_files_from_execution(session, file_paths, workdir)
