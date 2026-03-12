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

import pytz
from atlassian import Jira
from datetime import datetime, timedelta
from pydantic import AnyHttpUrl
from requests.exceptions import HTTPError
from typing import Any, Optional, Iterator, List

from langchain_core.documents import Document

from langchain_core.document_loaders import BaseLoader

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.configs import config, logger
from codemie.datasource.exceptions import MissingIntegrationException, UnauthorizedException


class JiraLoader(BaseLoader, BaseDatasourceLoader):
    """
    A Langchain loader for Jira.
    Supports both cloud and non-cloud

    Example for cloud:
    loader = JiraLoader(
        url="https://your-jira.example.com",
        jql="project=PROJ",
        username="<username>"
        token="<token>",
        cloud=True
    )

    Example for non cloud:
    loader = JiraLoader(
        url="https://your-jira.example.com",
        jql="project=PROJ",
        token="<token>",
        cloud=False
    )
    """

    FIELDS = 'summary,status,assignee,fixVersions,created,creator,updated,issuetype,description'
    MAX_RESULTS = 50
    JIRA_TIME_FORMAT = '%Y-%m-%d %H:%M'
    JIRA_UPDATED_FROM_TEMPLATE = "updatedDate >= '{}' AND "
    JIRA_PROFILE_TZ_FIELD = 'timeZone'
    JIRA_UPDATED_DATA_FIELD = 'updatedDate'
    JIRA_UPDATED_FIELD_ERROR = "JQL should not contain 'updatedDate' field for incremental reindex"
    DOCUMENTS_COUNT_KEY = 'documents_count_key'

    def __init__(
        self,
        jql: str,
        url: AnyHttpUrl,
        cloud: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
        updated_gte: Optional[datetime] = None,
    ):
        self.jql = jql
        self.url = url
        self.cloud = cloud
        self.username = username
        self.password = password
        self.token = token
        self.updated_gte = updated_gte

    def lazy_load(self) -> Iterator[Document]:
        """Loads the issues by JQL and returns Langchain Docs"""
        self._init_client()
        self._validate_creds()

        if self.updated_gte:
            if self.JIRA_UPDATED_DATA_FIELD in self.jql:
                raise ValueError(self.JIRA_UPDATED_FIELD_ERROR)

            self.jql = self._updated_filter_jql + self.jql

        issues = self._load_issues()

        for issue in issues:
            yield self._transform_to_doc(issue)

    def _validate_creds(self):
        """Validates that correct creds are initialized"""
        if self.cloud and (not self.username or not self.password):
            logger.error("Missing Url or Token for Cloud Confluence integration")
            raise MissingIntegrationException("Jira")

        if not self.cloud and not self.token:
            logger.error("Missing Url or Token for Confluence integration")
            raise MissingIntegrationException("Jira")

        try:
            self.jira.get_all_fields()
        except HTTPError as e:
            logger.error(f"Cannot authenticate user. Failed with error {e}")
            raise UnauthorizedException(datasource_type="Jira")

    def fetch_remote_stats(self) -> dict[str, Any]:
        self._init_client()
        self._validate_creds()

        if self.cloud:
            pages_count = self.jira.approximate_issue_count(self.jql)["count"]
        else:
            response = self.jira.jql(self.jql, start=0, limit=1)
            pages_count = response.get('total', 0)
        total_documents = pages_count  # No extra logic for now
        return {
            self.DOCUMENTS_COUNT_KEY: pages_count,
            self.TOTAL_DOCUMENTS_KEY: total_documents,
            self.SKIPPED_DOCUMENTS_KEY: total_documents - pages_count,
        }

    def _load_issues_for_cloud_jira(self):
        all_issues = []
        next_page_token = None
        while True:
            batch = self.jira.enhanced_jql(
                self.jql, fields=self.FIELDS, nextPageToken=next_page_token, limit=self.MAX_RESULTS
            )
            issues = batch['issues']
            all_issues.extend(issues)

            if batch['isLast']:
                break

            next_page_token = batch['nextPageToken']

        return all_issues

    def _load_issues_for_jira(self):
        start_at = 0
        all_issues = []

        while True:
            batch = self.jira.jql(self.jql, fields=self.FIELDS, start=start_at, limit=self.MAX_RESULTS)
            issues = batch['issues']
            all_issues.extend(issues)

            if len(issues) < self.MAX_RESULTS:
                break

            start_at += self.MAX_RESULTS

        return all_issues

    def _load_issues(self) -> List[dict]:
        """Load issues from Jira using the appropriate backend."""
        return self._load_issues_for_cloud_jira() if self.cloud else self._load_issues_for_jira()

    def _init_client(self):
        """Initializes Jira client with creds"""
        if self.cloud:
            self.jira = Jira(url=self.url, username=self.username, password=self.password, cloud=True, api_version=3)
        else:
            self.jira = Jira(url=self.url, token=self.token, cloud=False, api_version=2)

    def _transform_to_doc(self, issue: dict) -> Document:
        """Transforms Jira issue to Langchain document"""
        fields = issue.get('fields', {})
        key = issue.get('key')

        assignee = fields.get('assignee') or {}
        assignee_name = assignee.get('name', '')
        created = fields.get('created', 'No Creation Date')
        creator = fields.get('creator', {})
        # Handle cases where Jira API returns {"creator": null} by checking if the creator is not None.
        creator_name = creator.get('name', '') if creator else ''
        description = fields.get('description', '')
        fix_versions = fields.get('fixVersions', [])
        fix_versions_names = [version['name'] for version in fix_versions]
        fix_versions_str = ','.join(fix_versions_names)
        issue_type = fields.get('issuetype', {})
        issue_type_name = issue_type.get('name', 'Unknown')
        status = fields.get('status', {})
        status_name = status.get('name', 'No Status')
        summary = fields.get('summary', 'No Summary')
        updated = fields.get('updated', 'No Update Date')
        url = self.url.strip('/') + '/browse/' + key

        content = (
            f"Issue Key: {key}\n"
            f"Title: {summary}\n"
            f"URL: {url}\n"
            f"Status: {status_name}\n"
            f"Assignee: {assignee_name}\n"
            f"Created: {created}\n"
            f"Creator: {creator_name}\n"
            f"Updated: {updated}\n"
            f"Issue Type: {issue_type_name}\n"
            f"Fix Versions: {fix_versions_str}\n"
            f"Description: {description}\n"
        )

        return Document(page_content=content, metadata={'source': f"{key} - {summary}", 'key': key})

    def _get_jira_tz(self):
        """Returns Jira timezone"""
        return self.jira.myself()[self.JIRA_PROFILE_TZ_FIELD]

    @property
    def _updated_filter_jql(self):
        """Returns JQL for updated issues"""
        user_tz = pytz.timezone(config.TIMEZONE)
        jira_tz = pytz.timezone(self._get_jira_tz())

        update_time = self.updated_gte - timedelta(minutes=1)
        user_time = update_time.astimezone(user_tz)
        jira_time = user_time.astimezone(jira_tz)

        update_time_str = jira_time.strftime(self.JIRA_TIME_FORMAT)
        return self.JIRA_UPDATED_FROM_TEMPLATE.format(update_time_str)
