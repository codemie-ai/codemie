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

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class MetricsRequest(BaseModel):
    """Request model for sending custom metrics"""

    name: str = Field(..., description="The metric name (will be prefixed with 'frontend_' if not already)")
    attributes: Optional[Dict[str, Any]] = Field(default=None, description="Additional attributes for the metric")


class MetricsResponse(BaseModel):
    """Response model for metrics endpoint"""

    success: bool = Field(..., description="Whether the metric was sent successfully")
    message: str = Field(..., description="Result message")
