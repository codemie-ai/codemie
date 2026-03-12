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

import unittest
from unittest.mock import patch
from codemie.repository.repository_factory import FileRepositoryFactory, FileStorageType


class TestFileRepositoryFactory(unittest.TestCase):
    @patch('codemie.repository.repository_factory.config')
    def test_get_current_storage_type(self, mock_config):
        mock_config.FILES_STORAGE_TYPE = 'gcp'
        self.assertEqual(FileRepositoryFactory.get_current_storage_type(), FileStorageType.GCP)

    @patch('codemie.repository.repository_factory.config')
    def test_get_current_repository_aws(self, mock_config):
        mock_config.FILES_STORAGE_TYPE = 'aws'
        with patch('codemie.repository.repository_factory.AWSFileRepository') as mock_aws_repo:
            instance = mock_aws_repo.return_value
            assert FileRepositoryFactory.get_current_repository() == instance

    @patch('codemie.repository.repository_factory.config')
    def test_get_current_repository_gcp(self, mock_config):
        mock_config.FILES_STORAGE_TYPE = 'gcp'
        with patch('codemie.repository.repository_factory.GCPFileRepository') as mock_gcp_repo:
            instance = mock_gcp_repo.return_value
            self.assertEqual(FileRepositoryFactory.get_current_repository(), instance)

    @patch('codemie.repository.repository_factory.config')
    def test_get_current_repository_filesystem(self, mock_config):
        mock_config.FILES_STORAGE_TYPE = ''
        with patch('codemie.repository.repository_factory.FileSystemRepository') as mock_fs_repo:
            instance = mock_fs_repo.return_value
            self.assertEqual(FileRepositoryFactory.get_current_repository(), instance)

    @patch('codemie.repository.repository_factory.config')
    def test_get_current_repository_azure(self, mock_config):
        mock_config.FILES_STORAGE_TYPE = 'azure'
        with patch('codemie.repository.repository_factory.AzureFileRepository') as mock_azure_repo:
            instance = mock_azure_repo.return_value
            self.assertEqual(FileRepositoryFactory.get_current_repository(), instance)
