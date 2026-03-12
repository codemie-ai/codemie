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

from codemie_tools.base.models import ToolMetadata
from codemie_tools.open_api.models import OpenApiConfig

OPEN_API_TOOL = ToolMetadata(
    name="open_api",
    description="""
    Use this tool to invoke external API according to OpenAPI specification.
    If you do not have specification, retrieve it first by using another tool.
    All provided arguments must be in STRING format.
    You must provide the following required args: method: String, url: String.
    Other args are optional: headers: String, fields: String, body: String, filter_fields: String.
    IMPORTANT: "headers" and "fields" MUST be String text, example "fields": "{'param1': 'value'}"
    You can use filter_fields to extract only specific fields from the JSON response using dot notation,
    e.g., "filter_fields": "transcription,author,data.people"
    """.strip(),
    label="Invoke external API",
    user_description="""
    Allows the AI assistant to make calls to external APIs according to their OpenAPI specification. This tool enables interaction with a wide range of third-party services and custom APIs that follow the OpenAPI standard.
    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the API integration)
    2. Open API Spec
    3. API Key (optional, if required by the API)
    Usage Note:
    Use this tool when you need to interact with external services or APIs that have an OpenAPI specification. Often used in combination with "Get Open API spec" tool to first retrieve the specification if unknown.
    """.strip(),
    config_class=OpenApiConfig,
)

OPEN_API_SPEC_TOOL = ToolMetadata(
    name="open_api_spec",
    description="""
    Use this tool to get Open API specification that should be used to create proper
    HTTP request to external API per user request. Or if user asks to describe API capabilities etc.
    You must use this tool first before making request to get details about API specification.
    """,
    label="Get Open API spec",
    user_description="""
    Retrieves the OpenAPI (Swagger) specification for a given API endpoint. This tool helps in obtaining the necessary information to interact with an API using the "Invoke External API" tool.
    Usage Note:
    Use this tool when you need to obtain the OpenAPI specification for an API before using the "Invoke External API" tool. It's particularly useful when working with new or unfamiliar APIs.
    """.strip(),
    config_class=OpenApiConfig,
)
