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
from fastapi import Request

from codemie.rest_api.security.user import User


class BaseIdp(ABC):
    """Base class for identity providers"""

    @abstractmethod
    async def authenticate(self, request: Request) -> User:
        """Authenticate user and return User object

        Args:
            request: FastAPI request object containing headers and other auth data

        Returns:
            Authenticated User object
        """
        pass

    @abstractmethod
    def get_session_cookie(self) -> str:
        """Return session cookie name"""
        pass

    @staticmethod
    def _parse_attribute_to_list(value: str | list[str] | None) -> list[str]:
        """
        Parse SSO attribute value to a flat list of strings.

        Handles multiple formats:
        - None or empty: returns empty list
        - String: splits by comma and strips whitespace
        - List of strings: flattens any comma-separated elements

        Args:
            value: The attribute value from SSO (can be None, string, or list)

        Returns:
            A flat list of non-empty strings

        Examples:
            >>> BaseIdp._parse_attribute_to_list("app1,app2")
            ['app1', 'app2']
            >>> BaseIdp._parse_attribute_to_list(["app1", "app2,app3"])
            ['app1', 'app2', 'app3']
            >>> BaseIdp._parse_attribute_to_list(["app1"])
            ['app1']
        """
        if not value:
            return []

        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]

        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, str):
                    # Split each list element by comma in case it contains comma-separated values
                    result.extend([sub_item.strip() for sub_item in item.split(",") if sub_item.strip()])
            return result

        return []
