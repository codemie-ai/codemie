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

"""A fake reader that serves `bounded()` as itself, for handler tests that stub the reader's statements."""

from contextlib import asynccontextmanager
from typing import Any


def bounded_to_itself(repo: Any) -> Any:
    """Make `repo.bounded()` an async context manager yielding `repo` itself (no pool to acquire from)."""

    @asynccontextmanager
    async def bounded():
        yield repo

    @asynccontextmanager
    async def widened():
        yield repo

    repo.bounded = bounded
    repo.widened = widened
    return repo
