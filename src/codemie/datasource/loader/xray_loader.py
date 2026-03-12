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

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

from langchain_core.documents import Document
from langchain_core.document_loaders import BaseLoader
from requests.exceptions import HTTPError

from codemie.configs import config, logger
from codemie.datasource.exceptions import (
    ConnectionException,
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie_tools.qa.xray.xray_client import XrayClient


class XrayLoader(BaseLoader, BaseDatasourceLoader):
    """
    A Langchain loader for X-ray test cases.

    Example:
        loader = XrayLoader(
            jql="project=CALC AND type=Test",
            url="https://xray.cloud.getxray.app",
            client_id="<client_id>",
            client_secret="<client_secret>",
            limit=100,
            verify_ssl=True
        )
    """

    XRAY_TIME_FORMAT = '%Y-%m-%d %H:%M'
    XRAY_UPDATED_FROM_TEMPLATE = "updatedDate >= '{}' AND "
    XRAY_UPDATED_DATA_FIELD = 'updatedDate'
    XRAY_UPDATED_FIELD_ERROR = "JQL should not contain 'updatedDate' field for incremental reindex"
    DOCUMENTS_COUNT_KEY = 'documents_count_key'
    TOTAL_DOCUMENTS_KEY = 'total_documents'
    SKIPPED_DOCUMENTS_KEY = 'skipped_documents'

    def __init__(
        self,
        jql: str,
        url: str,
        client_id: str,
        client_secret: str,
        limit: int = 100,
        verify_ssl: bool = True,
        updated_gte: Optional[datetime] = None,
    ):
        self.jql = jql
        self.url = url
        self.client_id = client_id
        self.client_secret = client_secret
        self.limit = limit
        self.verify_ssl = verify_ssl
        self.updated_gte = updated_gte

    def lazy_load(self) -> Iterator[Document]:
        """Loads the tests by JQL and returns Langchain Docs"""
        self._init_client()
        self._validate_creds()

        if self.updated_gte:
            if self.XRAY_UPDATED_DATA_FIELD in self.jql:
                raise ValueError(self.XRAY_UPDATED_FIELD_ERROR)

            self.jql = self._updated_filter_jql + self.jql

        try:
            result = self.client.get_tests(jql=self.jql, max_results=None)
        except HTTPError as e:
            status = e.response.status_code if e.response else 'unknown'
            logger.error(f"X-ray HTTP error fetching tests: status={status}, error={e}", exc_info=True)
            raise UnauthorizedException(datasource_type="Xray")
        except OSError as e:
            logger.error(f"X-ray network error fetching tests: {e}", exc_info=True)
            raise ConnectionException(datasource_type="Xray", error_details=str(e))
        except Exception as e:
            logger.error(f"X-ray unexpected error fetching tests: {e}", exc_info=True)
            raise

        tests = result.get("tests", [])

        for test in tests:
            yield self._transform_to_doc(test)

    def _validate_creds(self):
        """Validates that correct creds are initialized"""
        if not self.client_id or not self.client_secret:
            logger.error("Missing client_id or client_secret for X-ray")
            raise MissingIntegrationException("Xray")

        try:
            self.client.health_check()
        except HTTPError as e:
            status = e.response.status_code if e.response else 'unknown'
            logger.error(f"X-ray authentication failed: status={status}, error={e}", exc_info=True)
            raise UnauthorizedException(datasource_type="Xray")
        except OSError as e:
            logger.error(f"X-ray network error on health check: {e}", exc_info=True)
            raise ConnectionException(datasource_type="Xray", error_details=str(e))

    def fetch_remote_stats(self) -> dict[str, Any]:
        self._init_client()
        self._validate_creds()

        try:
            result = self.client.get_tests(jql=self.jql, max_results=1)
        except HTTPError as e:
            status = e.response.status_code if e.response else 'unknown'
            logger.error(f"X-ray HTTP error fetching stats: status={status}, error={e}", exc_info=True)
            raise UnauthorizedException(datasource_type="Xray")
        except OSError as e:
            logger.error(f"X-ray network error fetching stats: {e}", exc_info=True)
            raise ConnectionException(datasource_type="Xray", error_details=str(e))
        except Exception as e:
            logger.error(f"X-ray unexpected error fetching stats: {e}", exc_info=True)
            raise

        tests_count = result.get("total_tests_count", 0)

        return {
            self.DOCUMENTS_COUNT_KEY: tests_count,
            self.TOTAL_DOCUMENTS_KEY: tests_count,
            self.SKIPPED_DOCUMENTS_KEY: 0,
        }

    def _init_client(self):
        """Initializes XrayClient with credentials"""
        self.client = XrayClient(
            base_url=self.url,
            client_id=self.client_id,
            client_secret=self.client_secret,
            limit=self.limit,
            verify_ssl=self.verify_ssl,
        )

    def _transform_to_doc(self, test: dict) -> Document:
        """Transforms X-ray test to Langchain document"""
        jira_fields = test.get("jira", {})
        key = jira_fields.get("key", "")
        summary = jira_fields.get("summary", "No Summary")
        project_id = test.get("projectId", "")

        test_type_obj = test.get("testType", {})
        test_type = test_type_obj.get("name", "Unknown")

        steps = test.get("steps", [])
        preconditions_data = test.get("preconditions", {})
        preconditions = self._normalize_preconditions(preconditions_data, key)

        unstructured = test.get("unstructured", "")
        gherkin = test.get("gherkin", "")
        url = f"{self.url.rstrip('/')}/browse/{key}"

        content_parts = [
            f"Test Key: {key}",
            f"Summary: {summary}",
            f"URL: {url}",
            f"Project ID: {project_id}",
            f"Test Type: {test_type}",
        ]

        self._append_steps_content(content_parts, steps)
        self._append_preconditions_content(content_parts, preconditions)

        if unstructured:
            content_parts.append(f"\nUnstructured: {unstructured}")

        if gherkin:
            content_parts.append(f"\nGherkin:\n{gherkin}")

        content = "\n".join(content_parts)

        return Document(
            page_content=content,
            metadata={
                "source": f"{key} - {summary}",
                "key": key,
                "test_type": test_type,
                "project_id": project_id,
            },
        )

    @staticmethod
    def _normalize_preconditions(preconditions_data: Any, test_key: str) -> list[dict]:
        """Normalizes preconditions field handling API type inconsistencies"""
        if isinstance(preconditions_data, dict):
            return preconditions_data.get("results", [])
        if isinstance(preconditions_data, list):
            logger.warning(f"X-ray API preconditions type mismatch: test_key={test_key}, type=list")
            return preconditions_data
        if preconditions_data is None:
            logger.warning(f"X-ray API preconditions is None: test_key={test_key}")
            return []
        actual_type = type(preconditions_data).__name__
        logger.warning(f"X-ray API preconditions type mismatch: test_key={test_key}, type={actual_type}")
        return []

    @staticmethod
    def _append_steps_content(content_parts: list[str], steps: list[dict]) -> None:
        """Appends formatted test steps to content parts"""
        if not steps:
            return

        content_parts.append("\nSteps:")
        for idx, step in enumerate(steps, 1):
            action = step.get("action", "")
            data = step.get("data", "")
            result = step.get("result", "")
            content_parts.append(f"{idx}. Action: {action}")
            if data:
                content_parts.append(f"   Data: {data}")
            content_parts.append(f"   Expected Result: {result}")

    @staticmethod
    def _append_preconditions_content(content_parts: list[str], preconditions: list[dict]) -> None:
        """Appends formatted preconditions to content parts"""
        if not preconditions:
            return

        content_parts.append("\nPreconditions:")
        for precond in preconditions:
            precond_jira = precond.get("jira", {})
            precond_key = precond_jira.get("key", "")
            if precond_key:
                content_parts.append(f"- {precond_key}")

    @property
    def _updated_filter_jql(self):
        """Returns JQL for updated tests"""
        user_tz = ZoneInfo(config.TIMEZONE)

        update_time = self.updated_gte - timedelta(minutes=1)
        user_time = update_time.astimezone(user_tz)

        update_time_str = user_time.strftime(self.XRAY_TIME_FORMAT)
        return self.XRAY_UPDATED_FROM_TEMPLATE.format(update_time_str)
