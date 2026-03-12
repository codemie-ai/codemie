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

from unittest.mock import Mock

from codemie.service.file_service.file_service import FileService


class TestFileService:
    def test_get_file_object(self, mocker):
        """Test the get_file_object method."""
        # Mock dependencies
        mock_file_repo = Mock()
        mock_file_object = Mock()

        mocker.patch(
            "codemie.service.file_service.file_service.FileRepositoryFactory.get_current_repository",
            return_value=mock_file_repo,
        )
        mocker.patch(
            "codemie.service.file_service.file_service.FileObject.from_encoded_url", return_value=mock_file_object
        )

        # Call the method
        FileService.get_file_object("encoded_file_name")

        # Verify the correct methods were called
        mock_file_repo.read_file.assert_called_once_with(
            file_name=mock_file_object.name, owner=mock_file_object.owner, mime_type=mock_file_object.mime_type
        )
