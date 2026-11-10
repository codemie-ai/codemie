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

"""Standalone core availability logging and OpenAPI filtering for guarded routes."""

from collections.abc import Sequence
from inspect import unwrap

from fastapi.dependencies.models import Dependant
from starlette.routing import BaseRoute

from codemie.configs.config import config
from codemie.configs.llm_provider_availability import core_availability
from codemie.configs.logger import logger
from codemie.rest_api.dependencies.core import require_core


def log_core_gating() -> None:
    """Log whether the LLM core is available (names only, never values). Standalone only."""
    available, provider, missing = core_availability(config)
    if available:
        logger.info(f"Core gating: check passed, provider={provider}")
    else:
        logger.warning(
            f"Core gating: missing settings: {', '.join(missing)}. "
            "Chats, Assistants and Workflows are off until they are set and the application is restarted."
        )


def _requires_core(dependant: Dependant) -> bool:
    """Inspect router, endpoint and nested dependencies, including functools.wraps wrappers."""
    return unwrap(dependant.call) is require_core or any(_requires_core(child) for child in dependant.dependencies)


def schema_routes(routes: Sequence[BaseRoute]) -> list[BaseRoute]:
    """Hide routes guarded by require_core while standalone core is off."""
    if not config.STANDALONE or core_availability(config)[0]:
        return list(routes)
    return [
        route
        for route in routes
        if (dependant := getattr(route, "dependant", None)) is None or not _requires_core(dependant)
    ]
