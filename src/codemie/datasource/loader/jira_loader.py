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

import json

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
from codemie.datasource.exceptions import (
    AmbiguousCustomFieldException,
    ConnectionException,
    InvalidCustomFieldException,
    MissingIntegrationException,
    UnauthorizedException,
)


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
        custom_fields: list[str] | None = None,
    ):
        self.jql = jql
        self.url = url
        self.cloud = cloud
        self.username = username
        self.password = password
        self.token = token
        self.updated_gte = updated_gte
        self.custom_fields = custom_fields
        self._all_fields_cache: list[dict] | None = None
        self._resolved_custom_fields: list[tuple[str, str]] = []

    def lazy_load(self) -> Iterator[Document]:
        """Loads the issues by JQL and returns Langchain Docs"""
        self._init_client()
        self._validate_creds()
        # Lenient: a field deleted after configuration must not kill a scheduled reindex
        self._resolve_custom_fields(strict=False)

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
        except OSError as e:
            # requests.ConnectionError / timeouts are OSError subclasses, not HTTPError
            logger.error(f"Jira network error: {e}")
            raise ConnectionException(datasource_type="Jira", error_details=str(e))

    def fetch_remote_stats(self) -> dict[str, Any]:
        self._init_client()
        self._validate_creds()
        # Strict: surface misconfigured custom fields at datasource creation/health-check time
        self._resolve_custom_fields(strict=True)

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

    def _get_all_fields_cached(self) -> list[dict]:
        """Returns all Jira fields, fetching them once per load run."""
        if self._all_fields_cache is None:
            self._all_fields_cache = self.jira.get_all_fields()
        return self._all_fields_cache

    def fetch_available_fields(self) -> list[dict]:
        """Connects to Jira and returns the raw field list (id, name, custom, schema)."""
        self._init_client()
        self._validate_creds()
        return self._get_all_fields_cached()

    @staticmethod
    def _index_fields_by_name(all_fields: list[dict]) -> dict[str, list[dict]]:
        """Maps each lowercased display name to every field carrying it.

        Jira allows several fields to share a name, so the value is a list. Catalogue entries
        without a name are skipped rather than aborting resolution for the whole instance.
        """
        by_name: dict[str, list[dict]] = {}
        for field in all_fields:
            name = field.get('name')
            if name:
                by_name.setdefault(name.lower(), []).append(field)
        return by_name

    @staticmethod
    def _match_field(entry: str, by_id: dict[str, dict], by_name: dict[str, list[dict]]) -> tuple[dict | None, bool]:
        """Matches one configured entry, returning the field and whether the name was ambiguous.

        An ID wins over a name and is never ambiguous. A name matching several fields resolves to
        the lowest field ID, so a scheduled reindex keeps indexing the same one.
        """
        field = by_id.get(entry)
        if field is not None:
            return field, False

        matches = by_name.get(entry.lower(), [])
        if not matches:
            return None, False
        return min(matches, key=lambda candidate: candidate['id']), len(matches) > 1

    @staticmethod
    def _report_unusable_entries(strict: bool, ambiguous: list[str], unresolved: list[str]) -> None:
        """Raises on strict resolution; logs and continues on the lenient index-time path."""
        if ambiguous:
            if strict:
                raise AmbiguousCustomFieldException(ambiguous)
            logger.warning(f"Ambiguous Jira custom field name(s), lowest field ID used: {', '.join(ambiguous)}")

        if unresolved:
            if strict:
                raise InvalidCustomFieldException(unresolved)
            logger.warning(f"Skipping unresolved Jira custom field(s): {', '.join(unresolved)}")

    def _resolve_custom_fields(self, strict: bool) -> None:
        """Resolves configured custom field IDs/names to (field_id, display_name) pairs.

        Each entry is matched as a field ID first (`customfield_10001`, `labels`), then as a
        case-insensitive field name. Jira allows several fields to share a display name, so a name
        matching more than one field is ambiguous: strict=True raises AmbiguousCustomFieldException,
        lenient mode picks the lowest field ID so a scheduled reindex keeps indexing the same field.
        With strict=True unresolved entries raise InvalidCustomFieldException; otherwise they are
        logged and skipped.
        """
        self._resolved_custom_fields = []
        if not self.custom_fields:
            return

        all_fields = self._get_all_fields_cached()
        by_id = {field['id']: field for field in all_fields}
        by_name = self._index_fields_by_name(all_fields)

        resolved: dict[str, str] = {}
        unresolved: list[str] = []
        ambiguous: list[str] = []
        for raw_entry in self.custom_fields:
            entry = raw_entry.strip()
            if not entry:
                continue
            field, is_ambiguous = self._match_field(entry, by_id, by_name)
            if is_ambiguous:
                ambiguous.append(entry)
            if field:
                resolved.setdefault(field['id'], field.get('name') or field['id'])
            else:
                unresolved.append(entry)

        self._report_unusable_entries(strict, ambiguous, unresolved)
        self._resolved_custom_fields = list(resolved.items())

    @property
    def _request_fields(self) -> str:
        """Default fields plus resolved custom field IDs, as the comma-joined `fields` API param."""
        if not self._resolved_custom_fields:
            return self.FIELDS
        return ','.join([self.FIELDS, *(field_id for field_id, _ in self._resolved_custom_fields)])

    def _load_issues_for_cloud_jira(self):
        all_issues = []
        next_page_token = None
        while True:
            batch = self.jira.enhanced_jql(
                self.jql, fields=self._request_fields, nextPageToken=next_page_token, limit=self.MAX_RESULTS
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
            batch = self.jira.jql(self.jql, fields=self._request_fields, start=start_at, limit=self.MAX_RESULTS)
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
        content += self._render_custom_fields(fields)

        return Document(page_content=content, metadata={'source': f"{key} - {summary}", 'key': key})

    def _render_custom_fields(self, fields: dict) -> str:
        """Renders resolved custom field values as labeled lines; absent/empty values are skipped."""
        lines = []
        for field_id, display_name in self._resolved_custom_fields:
            rendered = self._render_field_value(fields.get(field_id))
            if rendered:
                lines.append(f"{display_name}: {rendered}\n")
        return ''.join(lines)

    @classmethod
    def _render_field_value(cls, value: Any) -> str:
        """Renders an arbitrary Jira field value (scalar, dict, list, ADF, None) as plain text."""
        if value is None:
            return ''
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            rendered_items = [cls._render_field_value(item) for item in value]
            return ', '.join(item for item in rendered_items if item)
        if isinstance(value, dict):
            # Atlassian Document Format (rich text on Jira Cloud API v3)
            if value.get('type') and 'content' in value:
                return cls._render_adf_value(value)
            for display_key in ('displayName', 'value', 'name'):
                if value.get(display_key) is not None:
                    return cls._render_field_value(value[display_key])
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    @classmethod
    def _render_adf_value(cls, node: dict) -> str:
        """Extracts plain text from an Atlassian Document Format node tree."""
        if node.get('type') == 'text':
            return node.get('text', '')
        parts = [cls._render_adf_value(child) for child in (node.get('content') or []) if isinstance(child, dict)]
        separator = '\n' if node.get('type') in ('doc', 'paragraph') else ' '
        return separator.join(part for part in parts if part).strip()

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
