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

from concurrent.futures import ThreadPoolExecutor

from fastapi import status

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException

executor = ThreadPoolExecutor(max_workers=config.THREAD_POOL_MAX_WORKERS)


def raise_access_denied(action: str):
    raise ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message="Access denied",
        details=f"You do not have the necessary permissions to {action} this entity.",
        help="Please ensure you have the correct role or permissions assigned to your account. "
        "If you believe this is an error, contact your system administrator.",
    )


def raise_forbidden(action: str):
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details=f"You do not have the necessary permissions to {action} this entity.",
        help="Please ensure you have the correct role or permissions assigned to your account. "
        "If you believe this is an error, contact your system administrator.",
    )


def raise_unprocessable_entity(action: str, resource: str, exc: Exception):
    raise ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message=f"Failed to {action} a {resource}",
        details=f"An error occurred while trying to {action} a {resource}: {str(exc)}",
        help="Please check your request format and try again. If the issue persists, contact support.",
    ) from exc


def raise_not_found(resource_id: str, resource_type: str):
    raise ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message=f"{resource_type} not found",
        details=f"The {resource_type} with ID [{resource_id}] could not be found in the system.",
        help="Please ensure the specified ID is correct",
    )


def run_in_thread_pool(func, *args):
    future = executor.submit(func, *args)
    return future


def remove_nulls(obj):
    if isinstance(obj, dict):
        return {k: remove_nulls(v) for k, v in obj.items() if v is not None}

    elif isinstance(obj, list):
        return [remove_nulls(i) for i in obj if i is not None]

    return obj
