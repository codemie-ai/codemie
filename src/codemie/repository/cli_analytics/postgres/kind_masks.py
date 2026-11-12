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

"""The priced token kinds of a (session, model) token group, in one place.

The cost key (`cost_key_sql`), the kind flags and the stored `session_dims.kind_masks` all read the same kinds, so
the SQL spelling of the kinds and the bit each kind owns live here. A group `g` is a row of
`cost_key_sql.model_token_groups`.
"""

from __future__ import annotations

# The priced kinds of a group row `g`, in `kind_tokens` order (input, output, cache read, cache write = 5m + unsplit,
# cache write 1h), with the same clamp: the split never prices more than the write it splits.
_CACHE_1H = "least(coalesce(g.cache_creation_1h_tokens, 0), g.cache_creation_tokens)"
_CACHE_5M = f"least(coalesce(g.cache_creation_5m_tokens, 0), g.cache_creation_tokens - {_CACHE_1H})"
KIND_TOKENS = (
    "g.input_tokens",
    "g.output_tokens",
    "g.cache_read_tokens",
    f"({_CACHE_5M} + (g.cache_creation_tokens - {_CACHE_1H} - {_CACHE_5M}))",
    _CACHE_1H,
)
RATE_COLUMNS = ("input", "output", "cache_read", "cache_write", "cache_write_1h")
# One bit per kind, in the order of `RATE_COLUMNS`.
KIND_BITS = {column: 1 << index for index, column in enumerate(RATE_COLUMNS)}


def kind_mask_sql() -> str:
    """Integer expression over a group row `g`: the sum of the bits of the kinds the group used (tokens > 0)."""
    return " + ".join(
        f"CASE WHEN {tokens} > 0 THEN {KIND_BITS[column]} ELSE 0 END"
        for tokens, column in zip(KIND_TOKENS, RATE_COLUMNS, strict=True)
    )
