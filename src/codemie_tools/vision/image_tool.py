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

from typing import Type, Any, Tuple, List

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.file_object import FileObject
from codemie_tools.vision.tool_vars import IMAGE_TOOL

VISION_PROMPT = """
  Analyze and provide a detailed description of the image.
  If the image contains text, transcribe the text as well.

  Expected output:
  <description>
  Transcribed text: <transcribed_text>
"""

MAX_TOKENS = 1_000


class Input(BaseModel):
    query: str = Field(
        description="Detailed user query for image recognition",
    )


class ImageTool(CodeMieTool):
    """Calls gpt-vision to interpret and transcribe image contents"""

    args_schema: Type[BaseModel] = Input

    name: str = IMAGE_TOOL.name
    label: str = IMAGE_TOOL.label
    description: str = IMAGE_TOOL.description
    chat_model: Any = Field(exclude=True)
    files: List[FileObject] = Field(default_factory=list, exclude=True)

    @staticmethod
    def _process_single_image(image_content: Any, chat_model: Any, query: str) -> Tuple[str, str]:
        """Process a single image and return its analysis result"""
        try:
            result = chat_model.invoke(
                [
                    HumanMessage(
                        content=[
                            {"type": "text", "text": query if query else VISION_PROMPT},
                            {
                                "type": "image",
                                "source_type": "base64",
                                "data": image_content,
                                "mime_type": "image/png",
                            },
                        ]
                    )
                ],
                max_tokens=MAX_TOKENS,
            )
            return image_content, result.content
        except Exception as e:
            return image_content, f"Error processing image: {str(e)}"

    def execute(self, query: str, **kwargs):
        if not self.files:
            raise ValueError("No files provided")

        return self._process_files(query)

    def _process_files(self, query: str) -> str:
        """Process multiple image files and combine results"""
        if not self.files:
            raise ValueError(f"{self.name} requires at least one file to process.")

        # If there's only one file, process it directly and return the result
        if len(self.files) == 1:
            base64_content = self.files[0].to_image_base64()
            _, result = self._process_single_image(base64_content, self.chat_model, query)
            return result

        # Process multiple files with formatted output for each
        results = []
        for i, file_object in enumerate(self.files, 1):
            base64_content = file_object.to_image_base64()
            _, image_result = self._process_single_image(base64_content, self.chat_model, query)

            # Format the output with file metadata
            results.append(f"### IMAGE {i}: {file_object.name} ###")
            results.append(image_result)

        # Combine all results with separators
        return "\n\n" + "\n\n".join(results)
