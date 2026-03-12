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
from typing import Any, Optional, Type, List

from codemie_tools.base.codemie_tool import CodeMieTool
from langchain_core.tools import ToolException
from pydantic import create_model, BaseModel, Field
from codemie.core.models import IdeToolDefinition, IdeToolArgsSchema, IdeToolArgument
from codemie.configs import logger, config
from codemie.clients.natsio import Client

import asyncio


class IdeTool(CodeMieTool):
    request_id: str = ""
    name: str = ""
    description: str = ""
    args_schema: Type[BaseModel] = BaseModel
    definition: IdeToolDefinition = BaseModel
    client: Client = BaseModel

    def __init__(self, definition: IdeToolDefinition, request_id: str):
        super().__init__()
        self.request_id = request_id
        self.definition = definition
        self.name = self.definition.name
        self.description = self.definition.description
        self.args_schema = self.model_args()
        self.client = Client()

    def query_encode(self, obj):
        if isinstance(obj, BaseModel):
            return obj.__dict__

        return obj

    def execute(self, *args, **kwargs):
        request = json.dumps(
            {
                "tool_name": self.name,
                "query": json.dumps(kwargs, default=self.query_encode),
                "request_id": self.request_id,
            }
        )

        return asyncio.run(self.run_request(request))

    async def run_request(self, request: str) -> Any:
        nc = await self.client.connect()
        try:
            logger.info("Consumer tool %s is requesting subject %s", self.name, self.definition.subject)
            response = await nc.request(
                self.definition.subject, request.encode("utf-8"), timeout=config.NATS_PLUGIN_TOOL_TIMEOUT
            )
            return response.data.decode("utf-8")
        except Exception as e:
            return f"{e}"
        finally:
            await nc.close()

    def model_args(self):
        """Create a Pydantic model based on the provided schema."""

        return IdeTool.schema_to_model(self.definition.args_schema, f"{self.name}Arguments")

    @classmethod
    def schema_to_model(cls, schema: IdeToolArgsSchema, schema_name: str):
        properties = schema.properties if schema.properties else {}
        required = schema.required if schema.required else []

        fields = {}
        for name, tool_argument in properties.items():
            param_type = cls.convert_type(tool_argument, name)
            if name not in required:
                param_type = Optional[param_type]

            fields[name] = (
                param_type,
                Field(description=tool_argument.description) if tool_argument.description else None,
            )

        model = create_model(schema_name, __base__=BaseModel, **fields)
        return model

    @classmethod
    def convert_type(cls, param: IdeToolArgument, name: str):
        type_mapping = {
            "string": str,
            "integer": int,
            "boolean": bool,
            "number": float,
        }
        if param.type in type_mapping:
            return type_mapping[param.type]
        if param.type == "array":
            if not param.schema:
                return list
            generic_type_argument = param.schema.properties["genericType"]
            generic_type = cls.convert_type(generic_type_argument, name)
            return List[generic_type]
        if param.type == "object":
            if not param.schema:
                return dict
            return cls.schema_to_model(param.schema, f"{name}Arguments")
        raise ToolException(f"Invalid schema type {param.type}")
