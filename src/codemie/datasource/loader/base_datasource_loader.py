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

from abc import ABC, abstractmethod
from typing import Any, Iterator

from langchain_core.documents import Document


class BaseDatasourceLoader(ABC):
    DOCUMENTS_COUNT_KEY = 'documents_count_key'
    TOTAL_DOCUMENTS_KEY = 'total_documents'
    SKIPPED_DOCUMENTS_KEY = 'skipped_documents'

    @abstractmethod
    def fetch_remote_stats(self) -> dict[str, Any]:
        """
        Count the total number of documents to index for a given datasource.

        This method should be implemented by subclasses to correctly count documents to index
        and provide additional relevant statistics.

        Returns:
            Dict[str, Any]: A dictionary containing the total pages count under the key 'DOCUMENTS_COUNT_KEY'
                            and any other additional information as required by the subclass.
        """
        pass

    @abstractmethod
    def lazy_load(self) -> Iterator[Document]:
        pass
