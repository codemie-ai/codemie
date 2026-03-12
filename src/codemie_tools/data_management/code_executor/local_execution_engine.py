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

import base64
import logging
import mimetypes
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import ToolException

from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig

logger = logging.getLogger(__name__)

# Injected into user code during local subprocess execution.
# Intercepts plt.show() to capture matplotlib figures as PNG files
# saved to the current working directory (work_dir set via cwd= in subprocess.run).
_MATPLOTLIB_CAPTURE_SETUP = """\
_codemie_image_counter = [0]
try:
    import matplotlib as _matplotlib
    _matplotlib.use('Agg')
    import matplotlib.pyplot as _plt
    def _codemie_plt_show(*args, **kwargs):
        _path = f"image_{_codemie_image_counter[0]:04d}.png"
        _codemie_image_counter[0] += 1
        _plt.savefig(_path, bbox_inches="tight")
        _plt.close("all")
    _plt.show = _codemie_plt_show
except ImportError:
    pass
"""


class LocalExecutionEngine:
    """Executes Python code via subprocess with matplotlib image capture."""

    def __init__(
        self,
        config: CodeExecutorConfig,
        file_repository: Any | None,
        user_id: str,
    ) -> None:
        self.config = config
        self.file_repository = file_repository
        self.user_id = user_id

    def _export_one_created_file(self, file_path: str, work_path: Path) -> str | None:
        full_path = (work_path / file_path).resolve()
        if not full_path.is_relative_to(work_path.resolve()):
            logger.warning(f"Skipping export of '{file_path}': path escapes work directory")
            return

        if not full_path.exists():
            logger.warning(f"Skipping export of '{file_path}': file not found")
            return

        if not full_path.is_file():
            logger.warning(f"Skipping export of '{file_path}': not a file")
            return

        # Read file content
        content = full_path.read_bytes()

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(str(full_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        # Generate unique filename
        original_name = full_path.name
        unique_name = f"{uuid.uuid4()}_{original_name}"

        # Write to file repository
        stored_file = self.file_repository.write_file(
            name=unique_name,
            mime_type=mime_type,
            content=content,
            owner=self.user_id,
        )

        # Generate URL
        url = f"sandbox:/v1/files/{stored_file.to_encoded_url()}"
        logger.debug(f"Exported file '{file_path}' as '{unique_name}' with URL: {url}")
        return url

    def _export_created_files(self, export_files: list[str], work_dir: str) -> list[str]:
        """Write export files from work directory to file repository.

        Args:
            export_files: List of file paths relative to work_dir to export
            work_dir: Working directory where files were created

        Returns:
            List of file URLs for successfully exported files

        Raises:
            ValueError: If file_repository is not configured
        """
        if not self.file_repository:
            raise ValueError("File repository is required to export files")

        exported_urls: list[str] = []
        work_path = Path(work_dir)

        for file_path in export_files:
            try:
                exported_file_url = self._export_one_created_file(file_path, work_path)
                if exported_file_url:
                    exported_urls.append(exported_file_url)
            except Exception as e:
                logger.error(f"Failed to export file '{file_path}': {e}", exc_info=True)
                continue
        return exported_urls

    def execute(self, code: str, export_files: list[str] | None, input_files: list[FileObject] | None = None) -> str:
        """Execute Python code via subprocess with matplotlib image capture.

        Args:
            code: The Python code to execute
            export_files: Optional list of file paths to export from work directory after execution
            input_files: Optional list of files to write to the working directory before execution

        Returns:
            Execution result concatenated with exported file URLs (if any)

        Raises:
            ToolException: If execution fails or times out
        """
        tmp_path = None
        try:
            with tempfile.TemporaryDirectory() as work_dir:
                for file_obj in input_files or []:
                    content = file_obj.bytes_content()
                    if content is None:
                        logger.warning(f"Skipping file '{file_obj.name}': content is None")
                        continue
                    safe_name = Path(file_obj.name).name
                    dest = Path(work_dir) / safe_name
                    if not dest.resolve().is_relative_to(Path(work_dir).resolve()):
                        logger.warning(f"Skipping file '{file_obj.name}': path escapes work directory")
                        continue
                    with open(dest, "wb") as f:
                        f.write(content)

                uses_matplotlib = "matplotlib" in code
                full_code = (_MATPLOTLIB_CAPTURE_SETUP + "\n" + code) if uses_matplotlib else code
                with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
                    tmp.write(full_code)
                    tmp_path = tmp.name

                result = subprocess.run(
                    [sys.executable, tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=self.config.execution_timeout,
                    cwd=work_dir,
                )

                if result.returncode != 0:
                    raise ToolException(f"Code execution failed:\n{result.stderr}")

                # Handle matplotlib image generation
                output_text = result.stdout.strip() if result.stdout else "Code executed successfully with no output."

                # Handle export files if requested
                exported_urls: list[str] | None = None
                if export_files:
                    exported_urls = self._export_created_files(export_files, work_dir)
                if exported_urls:
                    export_text = "\n\nExported files:\n" + "\n".join(f"- {url}" for url in exported_urls)
                    output_text += export_text

                return output_text

        except subprocess.TimeoutExpired:
            raise ToolException(
                f"Code execution timed out after {self.config.execution_timeout} seconds. "
                "This may indicate an infinite loop or a resource-intensive operation."
            )
        except ToolException:
            raise
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error(
                f"Error executing code in local mode - Exception Type: {error_type}, Message: {error_msg}",
                exc_info=True,
            )
            raise ToolException(f"Error executing code: {error_type}: {error_msg}") from e
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
