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

import hashlib
import logging
import time
from json import dumps
from typing import List, Dict
from urllib.parse import urlencode

import jwt
import requests
from requests import HTTPError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 75
DEFAULT_BASE_URL = "https://prod-api.zephyr4jiracloud.com/connect"
DEFAULT_RELATIVE_PATH = "/public/rest/api/1.0"
JWT_EXPIRE_SEC = 3000


# API DOCS:
# General info https://zephyrdocs.atlassian.net/wiki/spaces/ZFJCLOUD/pages/1925120024/REST+API
# Interactive API https://zephyrsquad.docs.apiary.io/#reference/config/get-general-configuration
class ZephyrRestAPI(object):
    def __init__(
        self,
        account_id: str,
        access_key: str,
        secret_key: str,
        base_url: str = DEFAULT_BASE_URL,
        relative_path: str = DEFAULT_RELATIVE_PATH,
        timeout: str = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url
        self.relative_path = relative_path
        self.account_id = account_id
        self.access_key = access_key
        self.secret_key = secret_key
        self.timeout = int(timeout)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def request(
        self,
        method="GET",
        path="/",
        data=None,
        json=None,
        flags=None,
        params=None,
        headers=None,
        files=None,
    ):
        """Perform an HTTP request with flexible header support

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path
            data: Request data
            json: JSON data to send
            flags: URL flags
            params: URL parameters
            headers: Additional headers to include (will be merged with auth headers)
                    Use headers={'Content-Type': 'your/type'} to set content type
            files: Files to upload

        Returns:
            requests.Response: The HTTP response

        Note:
            Authentication headers (Authorization with JWT and zapiAccessKey) are
            automatically added and cannot be overridden by user headers.
        """
        method = method.upper()

        if not path.startswith("/"):
            path = "/" + path

        raw_url = self.base_url + self.relative_path + path
        url = self._prepare_url(raw_url, flags, params)

        if files is None:
            data = None if not data else dumps(data)

        headers = self._get_headers(method=method, path=path, headers=headers)
        response = requests.Session().request(
            method=method,
            url=url,
            headers=headers,
            data=data,
            json=json,
            timeout=self.timeout,
            files=files,
        )
        response.encoding = "utf-8"

        logger.debug("HTTP: %s %s -> %s %s", method, path, response.status_code, response.reason)
        logger.debug("HTTP: Response text -> %s", response.text)

        self._raise_for_status(response)
        return response

    @staticmethod
    def _prepare_url(url: str, flags: List = None, params: Dict = None):
        """Does some URL encoding magic"""
        if not flags:
            flags = []

        if not params:
            params = {}

        params_already_in_url = "?" in url

        if params or flags:
            if params_already_in_url:
                url += "&"
            else:
                url += "?"
        if params:
            url += urlencode(params or {})

        if flags:
            url += ("&" if params or params_already_in_url else "") + "&".join(flags or [])

        return url

    def _get_headers(self, method: str, path: str, headers: dict = None) -> Dict:
        """Generate headers with proper authentication"""
        # Generate JWT for authentication
        jwt_token = self._generate_jwt(method=method, path=path)

        # Start with user-provided headers (or empty dict)
        final_headers = headers.copy() if headers else {}

        # Add authentication headers (these cannot be overridden)
        final_headers.update({'Authorization': 'JWT ' + jwt_token, 'zapiAccessKey': self.access_key})

        return final_headers

    def _generate_jwt(self, method: str, path: str, jwt_expire_sec: int = JWT_EXPIRE_SEC) -> str:
        """Generate JWT token for header"""
        if "?" not in path:  # Without we get 401
            path += "?"

        canonical_path = (method + '&' + self.relative_path + path).replace('?', '&')

        payload_token = {
            'sub': self.account_id,
            'qsh': hashlib.sha256(canonical_path.encode('utf-8')).hexdigest(),
            'iss': self.access_key,
            'exp': int(time.time()) + jwt_expire_sec,
            'iat': int(time.time()),
        }
        return jwt.encode(payload_token, self.secret_key, algorithm='HS256').strip()

    def _raise_for_status(self, response):
        """Checks the response for errors and throws an exception if return code >= 400"""
        if response.status_code == 401 and response.headers.get("Content-Type") != "application/json;charset=UTF-8":
            raise HTTPError("Unauthorized (401)", response=response)

        if 400 <= response.status_code < 600:
            try:
                error_msg = self._handle_error_message(response.json())
            except Exception as e:
                logger.error(e)
                response.raise_for_status()
            else:
                raise HTTPError(error_msg, response=response)
        else:
            response.raise_for_status()

    def _handle_error_message(self, json: dict) -> str:
        """Hadles error messages from API response"""
        error_msg_list = json.get("errorMessages", [])
        errors = json.get("errors", {})

        if len(errors):
            if isinstance(errors, dict):
                error_msg_list.append(errors.get("message", ""))
            elif isinstance(errors, list):
                error_msg_list.extend([v.get("message", "") if isinstance(v, dict) else v for v in errors])

        if error := json.get("error"):
            error_msg_list.append(error)

        if error := json.get("clientMessage"):
            error_msg_list.append(error)

        return ",".join(error_msg_list)
