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
import pytz
from codemie.datasource.exceptions import MissingIntegrationException
from codemie.configs import config
from datetime import datetime
from unittest.mock import patch
from pydantic import AnyHttpUrl
from codemie.datasource.loader.jira_loader import JiraLoader
from langchain_core.documents import Document


@pytest.fixture
def mock_jira():
    with patch('codemie.datasource.loader.jira_loader.Jira') as mock_jira:
        yield mock_jira()


@pytest.fixture
def loader(mock_jira):
    return JiraLoader(
        jql='project=TEST', url='https://jira.example.com', cloud=True, username='test_user', password='token'
    )


@pytest.fixture
def loader_incremental(mock_jira):
    updated_gte = datetime(2024, 6, 8)
    timezone = pytz.timezone('UTC')

    return JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='test_token',
        updated_gte=timezone.localize(updated_gte),
    )


def test_validate_creds_cloud(loader):
    loader._init_client()
    loader._validate_creds()


def test_validate_creds_non_cloud(mock_jira):
    jql = 'project=TEST'
    url = AnyHttpUrl('https://jira.example.com')
    token = 'test_token'
    loader = JiraLoader(jql=jql, url=url, cloud=False, token=token)

    loader._init_client()
    loader._validate_creds()


def test_validate_creds_invalid(mock_jira):
    jql = 'project=TEST'
    url = AnyHttpUrl('https://jira.example.com')
    username = 'test_user'
    loader = JiraLoader(jql=jql, url=url, cloud=True, username=username)
    with pytest.raises(MissingIntegrationException):
        loader._validate_creds()


@patch('codemie.datasource.loader.jira_loader.JiraLoader._load_issues')
def test_lazy_load(load_issues, loader):
    load_issues.return_value = [{'key': 'TEST-1', 'fields': {'summary': 'Issue summary', 'creator': {}}}]
    docs = list(loader.lazy_load())
    assert len(docs) == 1
    assert isinstance(docs[0], Document)
    assert 'Issue Key: TEST-1' in docs[0].page_content


@patch.object(config, 'TIMEZONE', 'UTC')
@patch('codemie.datasource.loader.jira_loader.JiraLoader._get_jira_tz')
@patch('codemie.datasource.loader.jira_loader.JiraLoader._load_issues')
def test_lazy_load_incremental(load_issues, jira_tz, loader_incremental):
    load_issues.return_value = [{'key': 'TEST-1', 'fields': {'summary': 'Issue summary', 'creator': {}}}]
    jira_tz.return_value = 'Europe/Kyiv'

    docs = list(loader_incremental.lazy_load())

    assert len(docs) == 1
    assert isinstance(docs[0], Document)
    assert 'Issue Key: TEST-1' in docs[0].page_content
    assert 'updatedDate >= \'2024-06-08 02:59\'' in loader_incremental.jql


def test_lazy_load_incremental_err(loader_incremental):
    loader_incremental.jql += ' AND updatedDate >= \'2024-06-08 02:59\''

    with pytest.raises(ValueError):
        list(loader_incremental.lazy_load())


def test_transform_to_doc(loader):
    issue = {
        'key': 'TEST-1',
        'fields': {
            'summary': 'Issue summary',
            'status': {'name': 'Open'},
            'assignee': {'name': 'assignee_name'},
            'created': '2023-01-01',
            'creator': {'name': 'creator_name'},
            'updated': '2023-01-02',
            'issuetype': {'name': 'Bug'},
            'description': 'Issue description',
            'fixVersions': [{'name': 'v1.0'}],
        },
    }
    doc = loader._transform_to_doc(issue)

    assert isinstance(doc, Document)
    assert 'Issue Key: TEST-1' in doc.page_content
    assert 'Title: Issue summary' in doc.page_content
    assert 'Status: Open' in doc.page_content
    assert 'Assignee: assignee_name' in doc.page_content
    assert 'Created: 2023-01-01' in doc.page_content
    assert 'Creator: creator_name' in doc.page_content
    assert 'Updated: 2023-01-02' in doc.page_content
    assert 'Issue Type: Bug' in doc.page_content
    assert 'Fix Versions: v1.0' in doc.page_content
    assert 'Description: Issue description' in doc.page_content
