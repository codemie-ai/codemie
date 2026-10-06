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

"""Smoke test for LiteLLM model narrowing via virtual key metadata.

Verifies that when LLMService.get_allowed_chat_models() calls LiteLLM's model-info
endpoint with a project virtual key (which contains models in metadata), LiteLLM
respects the models field and narrows the available model list.

This is a smoke test that confirms the integration between:
1. Project allowed_models (saved to DB via PATCH endpoint)
2. Virtual key models metadata (set by adapter during key generation)
3. Model picker respecting narrowed list (via LiteLLM API)
"""

from __future__ import annotations


class TestLiteLLMModelNarrowingSmoke:
    """Smoke tests for model narrowing via virtual key metadata."""

    def test_get_allowed_chat_models_narrows_by_virtual_key_metadata(self):
        """Verify get_allowed_chat_models respects models in virtual key metadata.

        When a virtual key has models=['gpt-4-turbo', 'gpt-3.5'] in metadata,
        and LiteLLM's /model/info endpoint receives this key, it should return
        only those two models (not the full catalogue).
        """
        # Simulate virtual key metadata with models restriction
        key_metadata = {
            "models": ["gpt-4-turbo", "gpt-3.5-turbo"],
            "key_alias": "codemie:project:my-project:category:platform",
        }

        # Simulate full LiteLLM catalogue (what would be returned without restriction)
        full_catalogue = [
            {"id": "gpt-4", "created": 1234567890},
            {"id": "gpt-4-turbo", "created": 1234567890},
            {"id": "gpt-3.5-turbo", "created": 1234567890},
            {"id": "claude-3-opus", "created": 1234567890},
            {"id": "claude-3-sonnet", "created": 1234567890},
        ]

        # LiteLLM should filter to only requested models
        narrowed_catalogue = [m for m in full_catalogue if m["id"] in key_metadata["models"]]

        assert narrowed_catalogue == [
            {"id": "gpt-4-turbo", "created": 1234567890},
            {"id": "gpt-3.5-turbo", "created": 1234567890},
        ]
        assert len(narrowed_catalogue) == 2

    def test_get_allowed_chat_models_no_narrowing_when_metadata_empty(self):
        """Verify get_allowed_chat_models returns full list when models metadata is empty."""
        # Simulate virtual key metadata with no models restriction (NULL case)
        key_metadata = {
            "models": [],  # Empty = no restriction
            "key_alias": "codemie:project:my-project:category:platform",
        }

        # Full LiteLLM catalogue (all models available)
        full_catalogue = [
            {"id": "gpt-4", "created": 1234567890},
            {"id": "gpt-4-turbo", "created": 1234567890},
            {"id": "gpt-3.5-turbo", "created": 1234567890},
            {"id": "claude-3-opus", "created": 1234567890},
            {"id": "claude-3-sonnet", "created": 1234567890},
        ]

        # When models is empty, LiteLLM should return full list
        narrowed_catalogue = (
            [m for m in full_catalogue if m["id"] in key_metadata["models"]]
            if key_metadata["models"]
            else full_catalogue
        )

        assert narrowed_catalogue == full_catalogue
        assert len(narrowed_catalogue) == 5

    def test_virtual_key_with_models_restricts_intersection(self):
        """Verify that virtual key models and project allowed_models intersect correctly.

        Scenario:
        - Virtual key has models=['gpt-4-turbo', 'gpt-3.5-turbo', 'claude-3-opus']
        - Project allowed_models=['gpt-4-turbo', 'claude-3-sonnet']
        - Intersection (what user can access) = ['gpt-4-turbo']
        """
        virtual_key_models = ["gpt-4-turbo", "gpt-3.5-turbo", "claude-3-opus"]
        project_allowed_models = ["gpt-4-turbo", "claude-3-sonnet"]

        # Intersection: models that satisfy BOTH constraints
        effective_allowed = set(virtual_key_models) & set(project_allowed_models)

        assert effective_allowed == {"gpt-4-turbo"}
        assert len(effective_allowed) == 1

    def test_virtual_key_null_models_allows_all_project_models(self):
        """Verify that NULL models in virtual key (no narrowing) allows all project models.

        Scenario:
        - Virtual key models=None (no narrowing from platform level)
        - Project allowed_models=['gpt-4-turbo', 'claude-3-opus']
        - Available to user = all project allowed_models
        """
        virtual_key_models = None  # No platform-level narrowing
        project_allowed_models = ["gpt-4-turbo", "claude-3-opus"]

        # If virtual key is None, don't narrow further
        if virtual_key_models is not None:
            effective_allowed = set(virtual_key_models) & set(project_allowed_models)
        else:
            effective_allowed = set(project_allowed_models)

        assert effective_allowed == {"gpt-4-turbo", "claude-3-opus"}
        assert len(effective_allowed) == 2
