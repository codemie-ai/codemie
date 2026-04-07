# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Leaderboard framework metadata loader.

Reads static dimension, component, tier, and intent descriptions from
config/leaderboard/framework_metadata.yaml and caches them in memory.
The file is read once per process and never reloaded.
"""

from __future__ import annotations

import logging

import yaml

from codemie.configs.config import config

logger = logging.getLogger(__name__)


_cached_metadata: dict | None = None


def _load_metadata() -> dict:
    """Load and cache the YAML file. Called once per process."""
    global _cached_metadata
    if _cached_metadata is not None:
        return _cached_metadata
    path = config.LEADERBOARD_FRAMEWORK_METADATA_PATH
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    logger.info(f"Loaded leaderboard framework metadata from {path}")
    _cached_metadata = data
    return data


def get_framework_metadata() -> dict:
    """Return the full framework metadata for the API response.

    Returns a dict with keys: framework, tiers, intents, dimensions.
    """
    return _load_metadata()


def get_dimension_metadata(dimension_id: str) -> dict:
    """Return metadata for a single dimension including component descriptions.

    Returns empty dict if the dimension ID is not found.
    """
    data = _load_metadata()
    return data.get("dimensions", {}).get(dimension_id, {})


def get_intent_by_id(intent_id: str) -> dict:
    """Return intent metadata by ID (e.g. 'sdlc_unicorn', 'cli_focused').

    Returns a fallback dict if the intent ID is not found.
    """
    data = _load_metadata()
    for intent in data.get("intents", []):
        if intent["id"] == intent_id:
            return intent
    return {
        "id": intent_id or "explorer",
        "label": intent_id or "Explorer",
        "emoji": "\U0001f331",
        "color": "#6b7280",
        "description": "",
    }


def get_tier_by_name(tier_name: str) -> dict:
    """Return tier metadata by name (e.g. 'pioneer', 'expert').

    Returns a fallback dict if the tier name is not found.
    """
    data = _load_metadata()
    for tier in data.get("tiers", []):
        if tier["name"] == tier_name:
            return tier
    return {
        "name": tier_name or "newcomer",
        "label": tier_name.capitalize() if tier_name else "Newcomer",
        "level": 1,
        "min_score": 0,
        "color": "#6b7280",
        "description": "",
    }
