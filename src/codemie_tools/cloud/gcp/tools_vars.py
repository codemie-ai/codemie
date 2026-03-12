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
from .models import GCPConfig

GCP_TOOL = ToolMetadata(
    name="GCP",
    description="""
    Tool for interacting with Google Cloud Platform (GCP) REST API.
    Requires service account credentials and OAuth 2.0 scopes for authentication.

    Capabilities:
    - Access to all Google Cloud Platform REST APIs
    - Manage GCP resources (Compute Engine, Cloud Storage, Cloud Functions, etc.)
    - Execute any GCP REST API operation
    - Automatic OAuth token management with service account
    - Support for multiple GCP services

    Usage:
    Provide HTTP method, list of OAuth scopes, full URL, and optional request arguments.
    The tool handles authentication and token refresh automatically using service account credentials.

    Important:
    - You MUST provide appropriate OAuth scopes for your requests
    - You must include PROJECT_ID in the URL if the API requires it
    - URLs must be googleapis.com endpoints

    Examples:
    - List Compute Engine instances:
      method="GET",
      scopes=["https://www.googleapis.com/auth/compute"],
      url="https://compute.googleapis.com/compute/v1/projects/{project}/zones/{zone}/instances"

    - Create Cloud Storage bucket:
      method="POST",
      scopes=["https://www.googleapis.com/auth/devstorage.read_write"],
      url="https://storage.googleapis.com/storage/v1/b?project={project}",
      optional_args={"json": {"name": "bucket-name"}}
    """.strip(),
    label="GCP",
    user_description="""
    Provides access to the GCP (Google Cloud Platform) API, allowing for management and interaction
    with various GCP resources and services. This tool enables the AI assistant to perform a wide
    range of operations within the Google Cloud environment.

    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the GCP account)
    2. GCP Service Account Key (JSON format)

    Usage Note:
    Use this tool when you need to manage GCP resources, deploy services, or retrieve information
    from your Google Cloud environment.
    """.strip(),
    settings_config=True,
    config_class=GCPConfig,
)
