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


class CallbackParentTracker:
    def __init__(self) -> None:
        self._default_parent_id: str | None = None
        self._parent_ids: dict[str | None, str | None] = {None: None}

    @property
    def default_parent_id(self) -> str | None:
        return self._default_parent_id

    @default_parent_id.setter
    def default_parent_id(self, value: str | None) -> None:
        self.set(value, author=None)

    def get(self, author: str | None = None) -> str | None:
        return self._parent_ids.get(author, self._default_parent_id)

    def set(self, parent_thought_id: str | None, author: str | None = None) -> None:
        if author is None:
            self._default_parent_id = parent_thought_id
        self._parent_ids[author] = parent_thought_id
