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
from typing import Type, Any, Dict, Optional

from openai.lib.azure import AzureOpenAI
from pydantic import model_validator, BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.file_system.tools_vars import GENERATE_IMAGE_TOOL


class AzureDalleAIConfig(BaseModel):
    api_version: str
    azure_endpoint: str
    api_key: str

    @model_validator(mode='before')
    def validate_config(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        for field in cls.model_fields:
            if field not in values or not values[field]:
                raise ValueError(f"{field} is a required field and must be provided.")
        return values


class GenerateImagesToolInput(BaseModel):
    image_description: str = Field(
        description="Detailed image description or detailed user ask for generating an image."
    )


class GenerateImageTool(CodeMieTool):
    name: str = GENERATE_IMAGE_TOOL.name
    description: str = GENERATE_IMAGE_TOOL.description
    args_schema: Type[BaseModel] = GenerateImagesToolInput
    model_id: str = "Dalle3"
    azure_dalle_config: Optional[AzureDalleAIConfig] = Field(exclude=True)

    def execute(self, image_description: str, *args, **kwargs) -> Any:
        if not self.azure_dalle_config:
            raise ValueError("AzureDalleAIConfig is not provided.")
        client = AzureOpenAI(
            api_version=self.azure_dalle_config.api_version,
            azure_endpoint=self.azure_dalle_config.azure_endpoint,
            api_key=self.azure_dalle_config.api_key,
        )

        result = client.images.generate(model=self.model_id, prompt=image_description, n=1)

        image_url = json.loads(result.model_dump_json())['data'][0]['url']
        return image_url
