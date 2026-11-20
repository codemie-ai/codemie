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

"""Reads a user's model override, remembering the answer for the duration of one request.

A chat resolves the model several times and a model list twice, and each resolution reads the override
through a new synchronous session, including the common case where the user has none. Inside a request scope
(see request_override_cache) the answer is remembered per (user, project), so a request reads it once.

The scope is a context variable holding a dict, so every thread that inherits the request's context shares it
(asyncio.to_thread does). A plain thread started inside a request does not inherit it and reads the database
each time, which is correct, only not cached. Outside any scope nothing is remembered.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import UUID

from codemie.rest_api.models.user_model_override import UserModelOverride

if TYPE_CHECKING:
    _Cache = dict[tuple[str, str], UserModelOverride | None]

_request_cache: ContextVar[_Cache | None] = ContextVar("user_model_override_request_cache", default=None)


@contextmanager
def request_override_cache() -> Iterator[None]:
    """Remember overrides read inside the block, for the duration of one request."""
    token = _request_cache.set({})
    try:
        yield
    finally:
        _request_cache.reset(token)


def clear_request_override_cache() -> None:
    """Forget what the current request has read; called when the request itself changes an override."""
    cache = _request_cache.get()
    if cache is not None:
        cache.clear()


def load_user_override(user_id: str | UUID, project_name: str) -> UserModelOverride | None:
    """The user's override for the project, or None when they have none."""
    key = (str(user_id), project_name)
    cache = _request_cache.get()
    if cache is not None and key in cache:
        return cache[key]
    override = UserModelOverride.get_by_fields({"user_id": key[0], "project_name": project_name})
    if cache is not None:
        cache[key] = override
    return override
