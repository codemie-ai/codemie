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
from codemie.service.codemie_export_service import CodemieExportService
from unittest.mock import patch


class TestCodemieExportService:
    @pytest.fixture(autouse=True)
    def setup_class(self, tmpdir):
        self.tmp_state_dir = str(tmpdir.mkdir("tmp_state_dir"))
        self.user = 'dummy-user-id'
        # Setup the mock attributes directly on CodemieExportService class
        CodemieExportService.tmp_state_dir = self.tmp_state_dir
        CodemieExportService.user = self.user

    @patch('codemie.service.codemie_export_service.CodemieExportService._client')
    def test_dump_projects(self, mock_client):
        mock_client().search.return_value = {
            'hits': {'hits': [{'_index': 'test_index', '_source': {'project_name': 'TestProject'}}]}
        }
        CodemieExportService.dump_projects(project_name='TestProject')
        mock_client().search.assert_called_once()

    @patch('codemie.service.codemie_export_service.CodemieExportService._client')
    def test_dump_index_status(self, mock_client):
        mock_client().search.return_value = {
            'hits': {'hits': [{'_index': 'test_index', '_source': {'context_name': 'TestContext'}}]}
        }
        CodemieExportService.dump_index_status(context_name='TestContext', project_name='TestProject')
        mock_client().search.assert_called_once()

    @patch('codemie.service.codemie_export_service.CodemieExportService._client')
    def test_dump_repositories(self, mock_client):
        mock_client().search.return_value = {
            'hits': {'hits': [{'_index': 'test_index', '_source': {'repository_name': 'TestRepository'}}]}
        }
        CodemieExportService.dump_repositories(context_name='TestRepository', project_name='TestProject')
        mock_client().search.assert_called_once()
