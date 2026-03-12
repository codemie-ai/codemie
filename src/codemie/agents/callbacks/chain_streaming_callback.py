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

from typing import Any

from langchain_core.callbacks import StreamingStdOutCallbackHandler

from codemie.chains.base import StreamedGenerationResult
from codemie.core.utils import extract_text_from_llm_output


class ChainStreamingCallback(StreamingStdOutCallbackHandler):
    def __init__(self, gen):
        super().__init__()
        self.gen = gen

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        self.gen.send(StreamedGenerationResult(generated_chunk=self._escape_message(token)).model_dump_json())

    def _escape_message(self, message: str) -> str:
        """Replace '}{', with '}{\u2002' so frontend can split it properly"""
        text = extract_text_from_llm_output(message)
        return text.replace("}{", "}_{")
