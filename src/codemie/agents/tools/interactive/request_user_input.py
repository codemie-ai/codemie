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

"""Built-in tool that shows interactive UI elements to the user in chat.

The tool's args schema is built dynamically from the assistant's interactive
features config so disabled element types are absent from the schema exposed
to the model. Executing the tool emits an ``interactive_request`` chunk into
the NDJSON stream and ends the agent turn (``return_direct``); the structured
user response arrives as the next chat message.
"""

import logging
import uuid
from typing import Optional

from codemie_tools.base.codemie_tool import CodeMieTool

from codemie.chains.base import StreamedGenerationResult
from codemie.core.interactive import (
    InteractiveFeaturesConfig,
    InteractiveRequest,
    build_surface_args_schema,
    validate_surface,
)

logger = logging.getLogger(__name__)

REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"


class RequestUserInputTool(CodeMieTool):
    name: str = REQUEST_USER_INPUT_TOOL_NAME
    description: str = (
        "Show interactive UI elements to the user and wait for their structured response. "
        "Call this when you need an explicit decision, option selection, or short-form input. "
        "This ends your current turn; the user's structured response arrives as the next message."
    )
    return_direct: bool = True
    config: InteractiveFeaturesConfig
    # Optional customer-config catalog override (feature->element-types); None uses the
    # registry defaults in core/interactive.py.
    catalog: Optional[dict] = None
    thread_generator: object = None

    def __init__(self, config: InteractiveFeaturesConfig, thread_generator, catalog=None, **kwargs):
        super().__init__(config=config, thread_generator=thread_generator, catalog=catalog, **kwargs)
        self.args_schema = build_surface_args_schema(config, catalog)

    def execute(self, surface: list, **kwargs) -> str:
        # Raises ValueError on disabled elements -> ToolException -> model retries
        elements = validate_surface(surface, self.config, self.catalog)
        request = InteractiveRequest(request_id=str(uuid.uuid4()), surface=elements)
        self.thread_generator.send(StreamedGenerationResult(interactive_request=request).model_dump_json())
        logger.info(f"Emitted interactive request {request.request_id}")
        return ""  # return_direct=True ends the agent turn with no extra text
