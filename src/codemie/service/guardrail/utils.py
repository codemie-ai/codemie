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

import json
from typing import Generator, List

from pydantic import BaseModel

from codemie.rest_api.models.guardrail import GuardrailEntity

MAX_PAYLOAD_BYTES = 70 * 1024  # 70 KB


class EntityConfig(BaseModel):
    """Configuration for a single entity to check guardrails against."""

    entity_type: GuardrailEntity
    entity_id: str
    project_name: str


# the input shouldnt really need chunking, but just to be on the safe side
def batch_content(chunks: List[str]) -> Generator[List[dict], None, None]:
    """
    Batches items so that each yielded list of items does not exceed MAX_PAYLOAD_BYTES.
    If a single chunk is too large, it is split into smaller items, each ≤ MAX_PAYLOAD_BYTES.
    No concatenation of different input chunks is performed.
    """
    batch = []
    batch_size = 0

    for chunk in chunks:
        item = {"text": {"text": chunk}}
        item_bytes = len(json.dumps(item).encode("utf-8"))

        if item_bytes > MAX_PAYLOAD_BYTES:
            # Yield any pending batch first before splitting the large chunk
            if batch:
                yield batch
                batch = []
                batch_size = 0

            # Split and yield the oversized chunk
            yield from _split_oversized_chunk(chunk)
            continue

        # Normal batching logic
        if batch_size + item_bytes > MAX_PAYLOAD_BYTES:
            if batch:
                yield batch
            batch = [item]
            batch_size = item_bytes
        else:
            batch.append(item)
            batch_size += item_bytes
    if batch:
        yield batch


def _split_oversized_chunk(chunk: str) -> Generator[List[dict], None, None]:
    """
    Split a chunk that exceeds MAX_PAYLOAD_BYTES into smaller sub-chunks.
    Each sub-chunk is yielded as its own single-item batch.
    """
    start = 0
    while start < len(chunk):
        # Dynamically find the largest sub-chunk that fits
        sub_len = max(1, MAX_PAYLOAD_BYTES // 4)

        while sub_len > 0:
            sub_chunk = chunk[start : start + sub_len]
            sub_item = {"text": {"text": sub_chunk}}
            sub_item_bytes = len(json.dumps(sub_item).encode("utf-8"))

            if sub_item_bytes <= MAX_PAYLOAD_BYTES:
                break
            sub_len -= 1

        if sub_len == 0:
            raise ValueError("Cannot split chunk to fit payload size limit.")

        # Yield the sub-chunk as its own batch
        yield [sub_item]
        start += sub_len
