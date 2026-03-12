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

from unittest.mock import Mock, patch, call

import pytest
from langchain_core.documents import Document

from codemie.configs import config
from codemie.datasource.code.docs_generation_service import DocsGenService, IndexInfo


@pytest.fixture
def docs_gen_service():
    return DocsGenService()


@pytest.fixture
def sample_document():
    return Document(
        page_content="Test content", metadata={'file_path': 'path/to/test_file.py', 'source': 'path/to/source.py'}
    )


@pytest.fixture
def sample_index_info():
    return IndexInfo(
        project_name="test_project",
        repo_name="test_repo",
        index_type="code",
        description="Test description",
    )


class TestDocsGenService:
    def test_get_app_folder(self):
        app_id = "test_app"
        expected_path = f"{config.REPOS_LOCAL_DIR}/{app_id}"

        with patch('os.path.exists') as mock_exists, patch('os.makedirs') as mock_makedirs:
            mock_exists.return_value = False
            result = DocsGenService._get_app_folder(app_id)

            assert result == expected_path
            mock_makedirs.assert_called_once_with(expected_path)

    def test_limit_output_content(self):
        test_output = "test" * 20000  # Create large string

        with patch('tiktoken.encoding_for_model') as mock_encoding:
            mock_encode = Mock()
            mock_encode.encode.return_value = list(range(DocsGenService.TOKEN_LIMIT + 1000))
            mock_encode.decode.return_value = "truncated_content"
            mock_encoding.return_value = mock_encode

            result = DocsGenService._limit_output_content(test_output)

            assert result == "truncated_content"
            mock_encode.encode.assert_called_once()

    def test_recursively_generate_readmes(self, docs_gen_service, sample_index_info):
        mock_walk_data = [
            ("/root/docs/dir2", [], ["file3.py"]),
        ]

        with (
            patch('os.walk') as mock_walk,
            patch.object(docs_gen_service, '_generate_readme_for_directory') as mock_gen_readme,
            patch.object(docs_gen_service, '_get_app_folder') as mock_get_folder,
        ):
            mock_walk.return_value = mock_walk_data
            mock_get_folder.return_value = "/root"

            docs_gen_service.recursively_generate_readmes(sample_index_info, "test_llm", "test_uuid")

            expected_calls = [
                call(path, dirs, files, "/root/test_repo/docs", "test_llm", "test_uuid")
                for path, dirs, files in mock_walk_data
            ]
            mock_gen_readme.assert_has_calls(expected_calls)

    def test_generate_readme_for_directory(self, docs_gen_service):
        with (
            patch.object(docs_gen_service, '_create_readme_header') as mock_header,
            patch.object(docs_gen_service, '_get_content_for_readme') as mock_content,
            patch.object(docs_gen_service, '_generate_readme') as mock_generate,
        ):
            mock_header.return_value = "# Test Header\n"
            mock_content.return_value = "Test Content"

            docs_gen_service._generate_readme_for_directory(
                dirpath="/root/docs/test",
                folders=["folder1"],
                files=["file1.py"],
                root_path="/root/docs",
                llm_name="test_llm",
                request_uuid="test_uuid",
            )

            mock_header.assert_called_once()
            mock_content.assert_called_once()
            mock_generate.assert_called_once_with(
                "# Test Header\n##Content:\nTest Content\n", "docs/test", "test_llm", "test_uuid"
            )

    def test_create_readme_header(self, docs_gen_service):
        test_cases = [
            {
                'dirpath': "/test/path",
                'folders': ["folder1", "folder2"],
                'files': ["file1.py", "file2.py"],
                'expected': "# path\n\nThis folder contains:\n##Subfolders:\nfolder1\nfolder2\n\n##Files:\nfile1.py\nfile2.py\n\n",
            },
            {'dirpath': "/test/empty", 'folders': [], 'files': [], 'expected': "# empty\n\nThis folder contains:\n"},
        ]

        for case in test_cases:
            result = docs_gen_service._create_readme_header(case['dirpath'], case['folders'], case['files'])
            assert result == case['expected']

    def test_format_list(self, docs_gen_service):
        test_cases = [
            {'items': ["item1", "item2", "item3"], 'expected': "item1\nitem2\nitem3"},
            {'items': [], 'expected': ""},
            {'items': ["single_item"], 'expected': "single_item"},
        ]

        for case in test_cases:
            result = docs_gen_service._format_list(case['items'])
            assert result == case['expected']

    def test_get_content_for_readme(self, docs_gen_service):
        with (
            patch.object(docs_gen_service, '_get_file_contents') as mock_file_contents,
            patch.object(docs_gen_service, '_get_folder_tree_and_contents') as mock_folder_contents,
        ):
            mock_file_contents.return_value = "file content"
            mock_folder_contents.return_value = "folder content"

            # Test with files only
            result1 = docs_gen_service._get_content_for_readme("test", [], ["file1.py"])
            assert result1 == "file content"
            mock_file_contents.assert_called_once()

            # Test with folders
            result2 = docs_gen_service._get_content_for_readme("test", ["folder1"], ["file1.py"])
            assert result2 == "folder content"
            mock_folder_contents.assert_called_once()

    def test_get_file_contents(self, docs_gen_service):
        docs_gen_service.git_actions = [
            {'file_path': 'test/file1.py', 'content': 'content1' * 200},
            {'file_path': 'test/file2.py', 'content': 'content2' * 200},
            {'file_path': 'other/file3.py', 'content': 'content3' * 200},
        ]

        result = docs_gen_service._get_file_contents('test')
        assert len(result.split('\n\n')) == 2
        assert all(len(content) <= 500 for content in result.split('\n\n'))

    def test_get_folder_tree_and_contents(self, docs_gen_service):
        with (
            patch.object(docs_gen_service, '_get_folder_tree') as mock_tree,
            patch.object(docs_gen_service, '_get_inner_readme_contents') as mock_contents,
        ):
            mock_tree.return_value = "tree structure"
            mock_contents.return_value = "inner contents"

            result = docs_gen_service._get_folder_tree_and_contents("test", ["folder1"])

            assert "Here a folder tree for repo" in result
            assert "tree structure" in result
            assert "inner contents" in result
            mock_tree.assert_called_once_with("test")
            mock_contents.assert_called_once_with("test", ["folder1"])

    def test_get_folder_tree(self, docs_gen_service):
        docs_gen_service.git_actions = [
            {'file_path': 'test/file1.py'},
            {'file_path': 'test/file2.py'},
            {'file_path': 'other/file3.py'},
        ]

        result = docs_gen_service._get_folder_tree('test')
        assert len(result.split('\n')) == 2
        assert all('test' in path for path in result.split('\n'))

    def test_get_inner_readme_contents(self, docs_gen_service):
        docs_gen_service.git_actions = [
            {'file_path': 'test/folder1/README', 'content': 'content1'},
            {'file_path': 'test/folder2/README', 'content': 'content2'},
            {'file_path': 'other/folder3/README', 'content': 'content3'},
        ]

        result = docs_gen_service._get_inner_readme_contents('test', ['folder1', 'folder2'])

        assert len(result.split('\n\n')) == 2
        assert all(len(content) <= 500 for content in result.split('\n\n') if content)
