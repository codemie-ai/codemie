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
import requests
from codemie.datasource.exceptions import (
    AmbiguousCustomFieldException,
    ConnectionException,
    InvalidCustomFieldException,
    MissingIntegrationException,
)
from codemie.configs import config
from datetime import datetime
from unittest.mock import patch
from pydantic import AnyHttpUrl
from codemie.datasource.loader.jira_loader import JiraLoader
from langchain_core.documents import Document

ALL_FIELDS = [
    {'id': 'summary', 'name': 'Summary', 'custom': False},
    {'id': 'customfield_10001', 'name': 'Story Points', 'custom': True, 'schema': {'type': 'number'}},
    {'id': 'customfield_10002', 'name': 'Test Steps', 'custom': True, 'schema': {'type': 'string'}},
]


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


def test_validate_creds_unreachable_instance(mock_jira, loader):
    mock_jira.get_all_fields.side_effect = requests.ConnectionError('Max retries exceeded')

    loader._init_client()
    with pytest.raises(ConnectionException) as exc_info:
        loader._validate_creds()

    assert 'Failed to connect to Jira' in str(exc_info.value)


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


@pytest.fixture
def loader_with_custom_fields(mock_jira):
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=['customfield_10001', 'test steps'],
    )
    loader._init_client()
    mock_jira.get_all_fields.return_value = ALL_FIELDS
    return loader


def test_resolve_custom_fields_by_id_and_name(loader_with_custom_fields):
    loader_with_custom_fields._resolve_custom_fields(strict=True)

    assert loader_with_custom_fields._resolved_custom_fields == [
        ('customfield_10001', 'Story Points'),
        ('customfield_10002', 'Test Steps'),
    ]


def test_resolve_custom_fields_dedupes(mock_jira):
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=['customfield_10001', 'Story Points', ' customfield_10001 '],
    )
    loader._init_client()
    mock_jira.get_all_fields.return_value = ALL_FIELDS

    loader._resolve_custom_fields(strict=True)

    assert loader._resolved_custom_fields == [('customfield_10001', 'Story Points')]


def test_resolve_custom_fields_strict_raises(mock_jira):
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=['customfield_99999', 'No Such Field', 'customfield_10001'],
    )
    loader._init_client()
    mock_jira.get_all_fields.return_value = ALL_FIELDS

    with pytest.raises(InvalidCustomFieldException) as exc_info:
        loader._resolve_custom_fields(strict=True)

    assert 'customfield_99999' in str(exc_info.value)
    assert 'No Such Field' in str(exc_info.value)


def test_resolve_custom_fields_lenient_skips(mock_jira):
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=['customfield_99999', 'customfield_10001'],
    )
    loader._init_client()
    mock_jira.get_all_fields.return_value = ALL_FIELDS

    loader._resolve_custom_fields(strict=False)

    assert loader._resolved_custom_fields == [('customfield_10001', 'Story Points')]


def test_resolve_custom_fields_builtin_by_id(mock_jira):
    """Built-in field IDs (e.g. 'labels') resolve even though they don't match customfield_*."""
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=['labels'],
    )
    loader._init_client()
    mock_jira.get_all_fields.return_value = ALL_FIELDS + [{'id': 'labels', 'name': 'Labels', 'custom': False}]

    loader._resolve_custom_fields(strict=True)

    assert loader._resolved_custom_fields == [('labels', 'Labels')]


def test_fetch_available_fields(mock_jira):
    loader = JiraLoader(jql='', url='https://jira.example.com', cloud=True, username='test_user', password='token')
    mock_jira.get_all_fields.return_value = ALL_FIELDS

    assert loader.fetch_available_fields() == ALL_FIELDS


def test_resolve_custom_fields_no_config(loader):
    loader._init_client()
    loader._resolve_custom_fields(strict=True)

    assert loader._resolved_custom_fields == []
    assert loader._request_fields == JiraLoader.FIELDS


def test_request_fields_extended(loader_with_custom_fields):
    loader_with_custom_fields._resolve_custom_fields(strict=True)

    assert loader_with_custom_fields._request_fields == (f'{JiraLoader.FIELDS},customfield_10001,customfield_10002')


def test_fetch_remote_stats_raises_on_unknown_custom_field(mock_jira):
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=['No Such Field'],
    )
    mock_jira.get_all_fields.return_value = ALL_FIELDS
    mock_jira.approximate_issue_count.return_value = {'count': 5}

    with pytest.raises(InvalidCustomFieldException):
        loader.fetch_remote_stats()


@patch('codemie.datasource.loader.jira_loader.JiraLoader._load_issues')
def test_lazy_load_lenient_with_unknown_custom_field(load_issues, mock_jira):
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=['No Such Field'],
    )
    mock_jira.get_all_fields.return_value = ALL_FIELDS
    load_issues.return_value = [{'key': 'TEST-1', 'fields': {'summary': 'Issue summary', 'creator': {}}}]

    docs = list(loader.lazy_load())

    assert len(docs) == 1
    assert 'Issue Key: TEST-1' in docs[0].page_content


def test_render_field_value_scalars():
    assert JiraLoader._render_field_value('text ') == 'text'
    assert JiraLoader._render_field_value(5) == '5'
    assert JiraLoader._render_field_value(2.5) == '2.5'
    assert JiraLoader._render_field_value(True) == 'True'
    assert JiraLoader._render_field_value(None) == ''


def test_render_field_value_dicts():
    assert JiraLoader._render_field_value({'value': 'High'}) == 'High'
    assert JiraLoader._render_field_value({'name': 'Sprint 1'}) == 'Sprint 1'
    assert JiraLoader._render_field_value({'displayName': 'John Doe', 'accountId': 'abc'}) == 'John Doe'
    # Dict without display keys falls back to JSON
    assert JiraLoader._render_field_value({'foo': 'bar'}) == '{"foo": "bar"}'


def test_render_field_value_lists():
    assert JiraLoader._render_field_value(['a', 'b']) == 'a, b'
    assert JiraLoader._render_field_value([{'value': 'One'}, {'value': 'Two'}, None]) == 'One, Two'
    assert JiraLoader._render_field_value([]) == ''


def test_render_field_value_adf():
    adf = {
        'type': 'doc',
        'version': 1,
        'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'First line'}]},
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Second line'}]},
        ],
    }
    assert JiraLoader._render_field_value(adf) == 'First line\nSecond line'


def test_transform_to_doc_with_custom_fields(loader_with_custom_fields):
    loader_with_custom_fields._resolve_custom_fields(strict=True)
    issue = {
        'key': 'TEST-1',
        'fields': {
            'summary': 'Issue summary',
            'creator': {},
            'customfield_10001': 8,
            'customfield_10002': 'Step 1: open page',
        },
    }

    doc = loader_with_custom_fields._transform_to_doc(issue)

    assert 'Story Points: 8' in doc.page_content
    assert 'Test Steps: Step 1: open page' in doc.page_content


def test_transform_to_doc_skips_absent_and_none_custom_fields(loader_with_custom_fields):
    loader_with_custom_fields._resolve_custom_fields(strict=True)
    issue = {
        'key': 'TEST-1',
        'fields': {'summary': 'Issue summary', 'creator': {}, 'customfield_10001': None},
    }

    doc = loader_with_custom_fields._transform_to_doc(issue)

    assert 'Story Points' not in doc.page_content
    assert 'Test Steps' not in doc.page_content


def test_transform_to_doc_without_custom_fields_unchanged(loader, loader_with_custom_fields):
    """Regression: output with no custom fields configured is identical to the pre-feature format."""
    issue = {'key': 'TEST-1', 'fields': {'summary': 'Issue summary', 'creator': {}}}

    baseline = loader._transform_to_doc(issue)

    loader_with_custom_fields.custom_fields = None
    loader_with_custom_fields._resolve_custom_fields(strict=True)
    assert loader_with_custom_fields._transform_to_doc(issue).page_content == baseline.page_content


@patch('codemie.datasource.loader.jira_loader.JiraLoader._load_issues')
def test_lazy_load_requests_custom_fields(load_issues, loader_with_custom_fields, mock_jira):
    load_issues.return_value = []
    list(loader_with_custom_fields.lazy_load())

    assert loader_with_custom_fields._request_fields == (f'{JiraLoader.FIELDS},customfield_10001,customfield_10002')


DUPLICATE_NAME_FIELDS = [
    {'id': 'summary', 'name': 'Summary', 'custom': False},
    {'id': 'customfield_10100', 'name': 'Sprint', 'custom': True, 'schema': {'type': 'array'}},
    {'id': 'customfield_10020', 'name': 'Sprint', 'custom': True, 'schema': {'type': 'array'}},
]


def _loader_with(mock_jira, custom_fields, all_fields=None):
    loader = JiraLoader(
        jql='project=TEST',
        url='https://jira.example.com',
        cloud=True,
        username='test_user',
        password='token',
        custom_fields=custom_fields,
    )
    loader._init_client()
    mock_jira.get_all_fields.return_value = ALL_FIELDS if all_fields is None else all_fields
    return loader


def test_resolve_custom_fields_strict_raises_on_ambiguous_name(mock_jira):
    """Two Jira fields share the display name, so only the field ID identifies one."""
    loader = _loader_with(mock_jira, ['Sprint'], DUPLICATE_NAME_FIELDS)

    with pytest.raises(AmbiguousCustomFieldException) as exc_info:
        loader._resolve_custom_fields(strict=True)

    assert 'Sprint' in str(exc_info.value)
    assert 'customfield_' in str(exc_info.value)


def test_ambiguous_custom_field_is_an_invalid_custom_field(mock_jira):
    """Callers catching InvalidCustomFieldException must also catch the ambiguous case."""
    loader = _loader_with(mock_jira, ['Sprint'], DUPLICATE_NAME_FIELDS)

    with pytest.raises(InvalidCustomFieldException):
        loader._resolve_custom_fields(strict=True)


def test_resolve_custom_fields_lenient_picks_lowest_id_for_ambiguous_name(mock_jira):
    """A scheduled reindex must keep indexing the same field rather than whichever came last."""
    loader = _loader_with(mock_jira, ['Sprint'], DUPLICATE_NAME_FIELDS)

    loader._resolve_custom_fields(strict=False)

    assert loader._resolved_custom_fields == [('customfield_10020', 'Sprint')]


def test_resolve_custom_fields_exact_id_wins_over_ambiguous_name(mock_jira):
    """An explicit ID is never ambiguous even when its display name is shared."""
    loader = _loader_with(mock_jira, ['customfield_10100'], DUPLICATE_NAME_FIELDS)

    loader._resolve_custom_fields(strict=True)

    assert loader._resolved_custom_fields == [('customfield_10100', 'Sprint')]


@pytest.mark.parametrize('bad_field', [{'id': 'customfield_10500'}, {'id': 'customfield_10500', 'name': None}])
def test_resolve_custom_fields_tolerates_field_without_name(mock_jira, bad_field):
    """A nameless catalogue entry must not abort resolution for the whole Jira instance."""
    loader = _loader_with(mock_jira, ['Story Points'], [*ALL_FIELDS, bad_field])

    loader._resolve_custom_fields(strict=True)

    assert loader._resolved_custom_fields == [('customfield_10001', 'Story Points')]


def test_render_adf_value_with_null_content():
    """An explicitly null 'content' must render as empty, not raise TypeError."""
    adf = {'type': 'doc', 'version': 1, 'content': None}

    assert JiraLoader._render_field_value(adf) == ''


def test_render_adf_value_with_null_content_nested():
    adf = {
        'type': 'doc',
        'version': 1,
        'content': [
            {'type': 'paragraph', 'content': None},
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Kept'}]},
        ],
    }

    assert JiraLoader._render_field_value(adf) == 'Kept'
