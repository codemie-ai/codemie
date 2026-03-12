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

from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

from codemie.service.llm_service.llm_service import llm_service


class InvokeParams(BaseModel):
    retry_count: Optional[int] = None
    output_limit: Optional[int] = None
    output_format: Optional[Literal["txt", "json"]] = None


class ToolInvokeRequest(BaseModel):
    project: str
    llm_model: str = Field(default=llm_service.default_llm_model)
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    tool_attributes: Dict[str, Any] = Field(default_factory=dict)
    tool_creds: Optional[Dict[str, Any]] = None
    datasource_id: Optional[str] = None
    params: Optional[InvokeParams] = None


class ToolInvokeResponse(BaseModel):
    output: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SchemaField(BaseModel):
    type: str
    required: bool


class ToolSchemaResponse(BaseModel):
    tool_name: str
    creds_schema: Dict[str, SchemaField]
    args_schema: Dict[str, SchemaField]


class CodeDatasourceSearchParams(BaseModel):
    top_k: int = 10
    with_filtering: bool = False
    user_input: Optional[str] = None


class DatasourceSearchInvokeRequest(BaseModel):
    query: str
    llm_model: str = Field(default=llm_service.default_llm_model)
    code_search_params: Optional[CodeDatasourceSearchParams] = None
    params: Optional[InvokeParams] = None
