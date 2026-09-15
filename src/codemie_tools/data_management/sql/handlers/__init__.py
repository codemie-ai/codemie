# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from typing import Any, Dict, List, Union

from codemie_tools.data_management.sql.models import SQLConfig


class DialectHandler(ABC):
    """Abstract base for dialect-specific database operations.

    Implement this class to add support for a new database dialect and
    register the implementation in ``_HANDLERS`` inside ``tools.py``.
    """

    @abstractmethod
    def healthcheck(self, config: SQLConfig) -> None:
        """Validate connectivity and credentials for this dialect."""

    @abstractmethod
    def create_connection(self, config: SQLConfig) -> Any:
        """Create and return a database connection or client for this dialect."""

    @abstractmethod
    def execute(self, client: Any, query: str) -> Union[List[Dict], str]:
        """Execute a query and return the results."""

    @abstractmethod
    def list_schema(self, client: Any, config: SQLConfig) -> Dict:
        """Return schema metadata (tables/columns or measurements/fields)."""

    @abstractmethod
    def error_hint(self, config: SQLConfig, exc: Exception) -> str:
        """Return a user-facing error message with context for the given exception."""
