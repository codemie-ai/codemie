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

from enum import Enum
from typing import Optional, Dict, Any

from pydantic import BaseModel


class ApplicationType(str, Enum):
    MODULE = "module"
    LINK = "link"
    IFRAME = "iframe"


class Application(BaseModel):
    name: str
    description: Optional[str] = None
    created_by: Optional[str] = None
    icon_url: Optional[str] = None
    slug: str
    entry: str
    type: ApplicationType
    arguments: Optional[Dict[str, Any]] = None
