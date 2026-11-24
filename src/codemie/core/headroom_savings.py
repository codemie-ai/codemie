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

"""Headroom compression savings value-object carried on token usage and persisted with messages.

Pure DTO, no dependency on the Headroom service package.
"""

from __future__ import annotations

from pydantic import BaseModel


class HeadroomSavings(BaseModel):
    """Token savings from Headroom compression: one event, or the merge of several events of one request."""

    tokens_original: int
    tokens_compacted: int
    tokens_saved: int
    mode: str
    strategy: str | None = None
    calls: int = 1

    def merged(self, other: HeadroomSavings) -> HeadroomSavings:
        """Sum two savings; the strategy is dropped and the mode becomes "mixed" when the two differ."""
        return HeadroomSavings(
            tokens_original=self.tokens_original + other.tokens_original,
            tokens_compacted=self.tokens_compacted + other.tokens_compacted,
            tokens_saved=self.tokens_saved + other.tokens_saved,
            mode=self.mode if self.mode == other.mode else "mixed",
            strategy=self.strategy if self.strategy == other.strategy else None,
            calls=self.calls + other.calls,
        )
