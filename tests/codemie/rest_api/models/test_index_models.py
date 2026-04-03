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

import pytest
from unittest.mock import MagicMock
from fastapi import UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from codemie.rest_api.models.index import (
    IndexKnowledgeBaseFileTypes,
    IndexKnowledgeBaseFileRequest,
    IndexKnowledgeBaseRequest,
)


class TestIndexKnowledgeBaseFileTypes:
    def test_values(self):
        result = IndexKnowledgeBaseFileTypes.values()
        assert result == [
            'pdf',
            'txt',
            'csv',
            'xml',
            'pptx',
            'docx',
            'xlsx',
            'html',
            'epub',
            'ipynb',
            'msg',
            'yaml',
            'yml',
            'json',
            'zip',
            'mp3',
            'jpg',
            'jpeg',
            'png',
        ]


class TestIndexKnowledgeBaseFileRequest:
    @pytest.fixture
    def mock_attrs(self):
        return {
            'name': 'test_name',
            'project_name': 'test_project_name',
            'description': 'test_description',
            'project_space_visible': True,
            'csv_separator': ',',
            'csv_start_row': 1,
            'csv_rows_per_document': 1,
            'user': None,
        }

    def test_validate_files_ok(self, mock_attrs):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 1024
        mock_file.filename = 'test_file.txt'

        assert IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])

    def test_validate_files_count_low(self, mock_attrs):
        with pytest.raises(RequestValidationError):
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=[])

    def test_validate_files_count_high(self, mock_attrs):
        mock_files = [MagicMock(spec=UploadFile) for _ in range(11)]

        with pytest.raises(RequestValidationError):
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_file_too_large(self, mock_attrs):
        mock_file = MagicMock(spec=UploadFile)
        mock_file.size = 1024 * 1024 * 1024 + 1
        mock_file.filename = 'test_file.jpg'

        with pytest.raises(RequestValidationError):
            IndexKnowledgeBaseFileRequest(**mock_attrs, files=[mock_file])


class TestIndexKnowledgeBaseRequest:
    @pytest.fixture
    def valid_base_data(self):
        return {
            'project_name': 'codemie',
            'description': 'description goes here',
            'project_space_visible': True,
        }

    @pytest.mark.parametrize(
        "invalid_name",
        [
            '_test_name',
            '-test_name',
            'test@name',
            'test$name',
            'test name',
        ],
    )
    def test_invalid_names(self, valid_base_data, invalid_name):
        test_data = {**valid_base_data, 'name': invalid_name}
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(**test_data)

    @pytest.mark.parametrize(
        "valid_name",
        [
            'test123',
            'Test123',
            'test-123',
            'test_123',
            'testName',
            'a123456',
        ],
    )
    def test_valid_names(self, valid_base_data, valid_name):
        test_data = {**valid_base_data, 'name': valid_name}
        try:
            IndexKnowledgeBaseRequest(**test_data)
        except ValidationError:
            pytest.fail(f"Validation failed for valid name '{valid_name}'")

    def test_name_min_length(self, valid_base_data):
        test_data = {**valid_base_data, 'name': 'tes'}
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(**test_data)

    def test_name_max_length(self, valid_base_data):
        test_data = {**valid_base_data, 'name': 'a' * 51}
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(**test_data)

    @pytest.mark.parametrize(
        "invalid_description",
        [
            "",
            "a" * 501,
        ],
    )
    def test_invalid_descriptions(self, invalid_description: str) -> None:
        with pytest.raises(ValidationError):
            IndexKnowledgeBaseRequest(
                name="test-kb", project_name="project1", description=invalid_description, project_space_visible=False
            )
