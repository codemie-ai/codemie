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

"""Elasticsearch repository for metrics analytics queries.

This module handles all direct interactions with Elasticsearch for metrics data,
executing ES|QL queries and aggregations.
"""

from __future__ import annotations

import logging
import time

from elasticsearch import ApiError, AsyncElasticsearch, NotFoundError
from fastapi import status

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException

logger = logging.getLogger(__name__)

# Error messages
ANALYTICS_QUERY_FAILED_MSG = "Analytics query failed"
ANALYTICS_QUERY_HELP_MSG = "Please try again or contact support if the issue persists."
QUERY_TOO_EXPENSIVE_MSG = "Query too expensive"
QUERY_TOO_EXPENSIVE_HELP_MSG = (
    "The query requires too much memory to execute. "
    "Try narrowing your time range, adding more specific filters, "
    "or reducing the amount of data being processed."
)

# Error detail prefixes
CIRCUIT_BREAKER_DETAILS_PREFIX = "Query requires too much memory"
ES_SERVICE_ERROR_DETAILS_PREFIX = "Elasticsearch service error"
UNEXPECTED_ERROR_DETAILS_PREFIX = "Unexpected error"

# Log message templates
LOG_CIRCUIT_BREAKER_MSG = "ES|QL query hit circuit breaker: {error}"
LOG_AGGREGATION_CIRCUIT_BREAKER_MSG = "Aggregation query hit circuit breaker: {error}"
LOG_SEARCH_CIRCUIT_BREAKER_MSG = "Search query hit circuit breaker: {error}"
LOG_ESQL_API_ERROR_MSG = "ES|QL query failed with ApiError: {error}"
LOG_AGGREGATION_API_ERROR_MSG = "Aggregation query failed with ApiError: {error}"
LOG_SEARCH_API_ERROR_MSG = "Search query failed with ApiError: {error}"
LOG_ESQL_UNEXPECTED_ERROR_MSG = "ES|QL query failed with unexpected error: {error}"
LOG_AGGREGATION_UNEXPECTED_ERROR_MSG = "Aggregation query failed with unexpected error: {error}"
LOG_SEARCH_UNEXPECTED_ERROR_MSG = "Search query failed with unexpected error: {error}"


class MetricsElasticRepository:
    """Repository for querying metrics from Elasticsearch."""

    def __init__(self):
        """Initialize repository with async Elasticsearch client."""
        self._client: AsyncElasticsearch = ElasticSearchClient.get_async_client()
        self._index = config.ELASTIC_METRICS_INDEX

    async def execute_esql_query(self, query: str, filter_query: dict | None = None) -> dict:
        """Execute ES|QL query with optional filters and return results.

        Args:
            query: ES|QL query string
            filter_query: Optional bool query filter to apply (for access control, time range, etc.)

        Returns:
            Raw Elasticsearch response dict with results

        Raises:
            ExtendedHTTPException: If query execution fails
        """
        try:
            logger.debug(f"Executing ES|QL query: query={query}, filter={filter_query}")
            start_time = time.time()

            # ES|QL supports a filter parameter for applying filters
            if filter_query:
                result = await self._client.esql.query(query=query, filter=filter_query)
            else:
                result = await self._client.esql.query(query=query)

            execution_time = (time.time() - start_time) * 1000
            logger.info(f"ES|QL query completed in {execution_time:.2f}ms")

            return result
        except ApiError as e:
            # Handle circuit breaker exceptions (429) - query too expensive
            if e.status_code == 429 or "circuit_breaking_exception" in str(e):
                logger.warning(LOG_CIRCUIT_BREAKER_MSG.format(error=str(e)))
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message=QUERY_TOO_EXPENSIVE_MSG,
                    details=f"{CIRCUIT_BREAKER_DETAILS_PREFIX}: {str(e)}",
                    help=QUERY_TOO_EXPENSIVE_HELP_MSG,
                ) from e
            # Handle other Elasticsearch API errors
            logger.exception(LOG_ESQL_API_ERROR_MSG.format(error=str(e)))
            raise ExtendedHTTPException(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message=ANALYTICS_QUERY_FAILED_MSG,
                details=f"{ES_SERVICE_ERROR_DETAILS_PREFIX}: {str(e)}",
                help=ANALYTICS_QUERY_HELP_MSG,
            ) from e
        except Exception as e:
            # Handle unexpected errors
            logger.exception(LOG_ESQL_UNEXPECTED_ERROR_MSG.format(error=str(e)))
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message=ANALYTICS_QUERY_FAILED_MSG,
                details=f"{UNEXPECTED_ERROR_DETAILS_PREFIX}: {str(e)}",
                help=ANALYTICS_QUERY_HELP_MSG,
            ) from e

    async def execute_aggregation_query(self, body: dict) -> dict:
        """Execute aggregation query and return results.

        Args:
            body: Elasticsearch query body with aggregations

        Returns:
            Raw Elasticsearch response dict with hits and aggregations

        Raises:
            ExtendedHTTPException: If query execution fails
        """
        try:
            logger.debug(f"Executing aggregation query on index {self._index}, body={body}")
            start_time = time.time()

            result = await self._client.search(index=self._index, body=body)

            execution_time = (time.time() - start_time) * 1000
            logger.info(f"Aggregation query completed in {execution_time:.2f}ms")

            return result
        except NotFoundError:
            logger.warning(f"Index {self._index} not found, returning empty results")
            return {"hits": {"hits": [], "total": {"value": 0}}, "aggregations": {}}
        except ApiError as e:
            # Handle circuit breaker exceptions (429) - query too expensive
            if e.status_code == 429 or "circuit_breaking_exception" in str(e):
                logger.warning(LOG_AGGREGATION_CIRCUIT_BREAKER_MSG.format(error=str(e)))
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message=QUERY_TOO_EXPENSIVE_MSG,
                    details=f"{CIRCUIT_BREAKER_DETAILS_PREFIX}: {str(e)}",
                    help=QUERY_TOO_EXPENSIVE_HELP_MSG,
                ) from e
            # Handle other Elasticsearch API errors
            logger.exception(LOG_AGGREGATION_API_ERROR_MSG.format(error=str(e)))
            raise ExtendedHTTPException(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message=ANALYTICS_QUERY_FAILED_MSG,
                details=f"{ES_SERVICE_ERROR_DETAILS_PREFIX}: {str(e)}",
                help=ANALYTICS_QUERY_HELP_MSG,
            ) from e
        except Exception as e:
            # Handle unexpected errors
            logger.exception(LOG_AGGREGATION_UNEXPECTED_ERROR_MSG.format(error=str(e)))
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message=ANALYTICS_QUERY_FAILED_MSG,
                details=f"{UNEXPECTED_ERROR_DETAILS_PREFIX}: {str(e)}",
                help=ANALYTICS_QUERY_HELP_MSG,
            ) from e

    async def execute_search_query(self, query: dict, size: int = 20, from_: int = 0) -> dict:
        """Execute search query with pagination.

        Args:
            query: Elasticsearch query dict
            size: Number of results to return
            from_: Offset for pagination

        Returns:
            Raw Elasticsearch response dict

        Raises:
            ExtendedHTTPException: If query execution fails
        """
        try:
            body = {"query": query, "size": size, "from": from_}
            logger.debug(f"Executing search query on index {self._index}, body={body}")
            start_time = time.time()
            result = await self._client.search(index=self._index, body=body)

            execution_time = (time.time() - start_time) * 1000
            logger.info(f"Search query completed in {execution_time:.2f}ms")

            return result
        except NotFoundError:
            logger.warning(f"Index {self._index} not found, returning empty results")
            return {"hits": {"hits": [], "total": {"value": 0}}}
        except ApiError as e:
            # Handle circuit breaker exceptions (429) - query too expensive
            if e.status_code == 429 or "circuit_breaking_exception" in str(e):
                logger.warning(LOG_SEARCH_CIRCUIT_BREAKER_MSG.format(error=str(e)))
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message=QUERY_TOO_EXPENSIVE_MSG,
                    details=f"{CIRCUIT_BREAKER_DETAILS_PREFIX}: {str(e)}",
                    help=QUERY_TOO_EXPENSIVE_HELP_MSG,
                ) from e
            # Handle other Elasticsearch API errors
            logger.exception(LOG_SEARCH_API_ERROR_MSG.format(error=str(e)))
            raise ExtendedHTTPException(
                code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message=ANALYTICS_QUERY_FAILED_MSG,
                details=f"{ES_SERVICE_ERROR_DETAILS_PREFIX}: {str(e)}",
                help=ANALYTICS_QUERY_HELP_MSG,
            ) from e
        except Exception as e:
            # Handle unexpected errors
            logger.exception(LOG_SEARCH_UNEXPECTED_ERROR_MSG.format(error=str(e)))
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message=ANALYTICS_QUERY_FAILED_MSG,
                details=f"{UNEXPECTED_ERROR_DETAILS_PREFIX}: {str(e)}",
                help=ANALYTICS_QUERY_HELP_MSG,
            ) from e
