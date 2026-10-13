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

"""Files that already carry content are not re-read by their sandbox path (EPMCDME-15558)."""

from pathlib import Path
from unittest.mock import MagicMock

from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.file_upload_service import FileUploadService


def test_file_with_content_is_not_re_read_from_the_repository(tmp_path):
    repository = MagicMock()
    service = FileUploadService(repository)
    file_object = FileObject(
        name="skills/foo-12345678/a.py", owner="skill-x", mime_type="text/x-python", content=b"print(1)"
    )

    service._download_files_to_temp([file_object], str(tmp_path))

    repository.read_file.assert_not_called()
    assert (Path(tmp_path) / "skills/foo-12345678/a.py").read_bytes() == b"print(1)"


def test_file_without_content_is_read_from_the_repository(tmp_path):
    repository = MagicMock()
    repository.read_file.return_value = FileObject(name="a.py", owner="o", mime_type="text/plain", content=b"x")
    service = FileUploadService(repository)

    service._download_files_to_temp([FileObject(name="a.py", owner="o", mime_type="text/plain")], str(tmp_path))

    repository.read_file.assert_called_once()
    assert (Path(tmp_path) / "a.py").read_bytes() == b"x"


def test_jobs_mode_input_bytes_use_carried_content_without_re_reading():
    from codemie_tools.data_management.code_executor.code_executor_tool import CodeExecutorTool

    repository = MagicMock()
    tool = CodeExecutorTool(file_repository=repository, user_id="u")
    file_object = FileObject(
        name="skills/foo-12345678/a.py", owner="skill-x", mime_type="text/x-python", content=b"print(1)"
    )

    result = tool._read_input_file_bytes([file_object])

    repository.read_file.assert_not_called()
    assert result == {"skills/foo-12345678/a.py": b"print(1)"}
