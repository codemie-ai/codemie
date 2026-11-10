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

"""Request-time guard for endpoints that require the LLM-backed core."""

from __future__ import annotations

from fastapi import HTTPException

from codemie.configs.config import config
from codemie.configs import llm_provider_availability


def require_core() -> None:
    """Return 404 when standalone core is unavailable; usable on routers and endpoints."""
    if config.STANDALONE and not llm_provider_availability.core_availability(config)[0]:
        raise HTTPException(status_code=404, detail="Not found")
