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

import logging
from typing import Any, List, Dict, Optional, Callable

from langchain_community.document_loaders import ConfluenceLoader
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential,
)

from codemie.datasource.loader.base_datasource_loader import BaseDatasourceLoader
from codemie.configs import logger


class ConfluenceDatasourceLoader(ConfluenceLoader, BaseDatasourceLoader):
    def fetch_remote_stats(self) -> dict[str, Any]:
        response = self.confluence.cql(self.cql, start=0, limit=1)
        if not isinstance(response, dict):
            raise ValueError("Cannot retrieve data with provided configuration")
        pages_count = response['totalSize']
        total_documents = pages_count  # No extra logic for now
        return {
            self.DOCUMENTS_COUNT_KEY: pages_count,
            self.TOTAL_DOCUMENTS_KEY: total_documents,
            self.SKIPPED_DOCUMENTS_KEY: total_documents - pages_count,
        }

    def _search_content_by_cql(
        self,
        cql: str,
        include_archived_spaces: Optional[bool] = None,
        **kwargs: Any,
    ) -> tuple[List[dict], str]:
        """Overriden to fix the bug.
        See https://github.com/langchain-ai/langchain/commit/0d20c314dd0508ea956482fbdd6ce7854b85fc01
        (!) IMPORTANT. Remove once underlying langchain_community is updated
        """
        if kwargs.get("next_url"):
            response = self.confluence.get(kwargs["next_url"])
        else:
            url = "rest/api/content/search"

            params: Dict[str, Any] = {"cql": cql}
            params.update(kwargs)
            if include_archived_spaces is not None:
                params["includeArchivedSpaces"] = include_archived_spaces

            response = self.confluence.get(url, params=params)

        return response.get("results", []), response.get("_links", {}).get("next", "")

    def paginate_request(self, retrieval_method: Callable, **kwargs: Any) -> List:
        """Overriden to fix the bug.
        See https://github.com/langchain-ai/langchain/commit/0d20c314dd0508ea956482fbdd6ce7854b85fc01
        (!) IMPORTANT. Remove once underlying langchain_community is updated
        """
        max_pages = kwargs.pop("max_pages")
        docs: List[dict] = []
        kwargs["next_url"] = ""

        while len(docs) < max_pages:
            get_pages = retry(
                reraise=True,
                stop=stop_after_attempt(
                    self.number_of_retries  # type: ignore[arg-type]
                ),
                wait=wait_exponential(
                    multiplier=1,
                    min=self.min_retry_seconds,  # type: ignore[arg-type]
                    max=self.max_retry_seconds,  # type: ignore[arg-type]
                ),
                before_sleep=before_sleep_log(logger, logging.WARNING),
            )(retrieval_method)

            if self.cql:
                batch, next_url = get_pages(**kwargs)
                if not next_url:
                    docs.extend(batch)
                    break
                kwargs["next_url"] = next_url
            else:
                batch = get_pages(**kwargs, start=len(docs))
                if not batch:
                    break
            docs.extend(batch)
        return docs[:max_pages]
