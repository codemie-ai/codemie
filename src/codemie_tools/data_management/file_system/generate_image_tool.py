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

from typing import Any, Optional

from pydantic import BaseModel, Field

from codemie_tools.base.artifact_publishing import ArtifactPublisher, PublishContext
from codemie_tools.base.artifact_storage import publish_artifact
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.file_system.image_generator import ImageGenerator
from codemie_tools.data_management.file_system.tools_vars import GENERATE_IMAGE_TOOL


class GenerateImagesToolInput(BaseModel):
    image_description: str = Field(
        description="Detailed image description or detailed user ask for generating an image."
    )


class GenerateImageTool(CodeMieTool):
    name: str = GENERATE_IMAGE_TOOL.name
    description: str = GENERATE_IMAGE_TOOL.description or ""
    args_schema: Any = GenerateImagesToolInput
    image_generator: Optional[Any] = Field(exclude=True, default=None)
    artifact_publisher: ArtifactPublisher | None = Field(exclude=True, default=None)
    publish_context: PublishContext | None = Field(exclude=True, default=None)

    def execute(self, image_description: str, *args, **kwargs) -> Any:
        if not self.image_generator:
            raise ValueError("Image generation is not configured.")
        url, b64_data = self.image_generator.generate(image_description)
        return self._resolve_output(url, b64_data)

    def _resolve_output(self, url: str | None, b64_data: str | None) -> str:
        if not url and not b64_data:
            raise ValueError("Image generation returned no image data.")
        if b64_data:
            return self._store_b64_image(b64_data)
        if url is None:
            raise ValueError("Image generation returned no image data.")
        return url

    def _store_b64_image(self, b64_data: str) -> str:
        result = publish_artifact(
            self.artifact_publisher,
            self.publish_context,
            "image.png",
            content=b64_data,
            mime="image/png",
            base64_content=True,
        )
        if not result.ok:
            raise ValueError(result.reason)
        return result.link
