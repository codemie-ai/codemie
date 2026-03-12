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

from typing import List

from langchain_core.messages import BaseMessage

from codemie.core.utils import calculate_tokens


def _create_message_batches(messages: List[BaseMessage], max_tokens: int) -> List[List[BaseMessage]]:
    """
    Creates batches of messages such that each batch does not exceed the maximum number of tokens.
    System message (first message) should not be included in the input messages.

    Args:
        messages (List[BaseMessage]): A list of messages to be batched (excluding system message)
        max_tokens (int): The maximum number of tokens per batch

    Returns:
        List[List[BaseMessage]]: A list of message batches
    """
    batches = []
    current_batch = []
    current_batch_tokens = 0

    for msg in messages:
        msg_tokens = calculate_tokens(str(msg))

        # If message itself is larger than max_tokens, it should be in its own batch
        if msg_tokens > max_tokens:
            if current_batch:  # Add the current batch if it's not empty
                batches.append(current_batch)
                current_batch = []
                current_batch_tokens = 0
            batches.append([msg])  # Add large message as its own batch
            continue

        if current_batch_tokens + msg_tokens > max_tokens:
            batches.append(current_batch)
            current_batch = []
            current_batch_tokens = 0

        current_batch.append(msg)
        current_batch_tokens += msg_tokens

    if current_batch:  # Add the last batch if it's not empty
        batches.append(current_batch)

    return batches
