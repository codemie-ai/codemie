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

import os
from pathlib import Path
from typing import List

from codemie.configs import config, logger
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.utils.default_applications import CODEMIE_PROJECT_NAME


class AssistantService:
    """
    Service class for managing assistants.

    This class provides methods for loading, caching, and retrieving assistant templates
    from specified template directories.

    Attributes:
        _cached_base_assistant_templates (dict): Cache for base assistant templates.
        _cached_admin_assistant_templates (dict): Cache for admin assistant templates.
    """

    _cached_base_assistant_templates = {}
    _cached_admin_assistant_templates = {}

    def __init__(self):
        """
        Initializes the AssistantService.

        Loads assistants templates from the specified template directories and caches them.
        """
        templates_dir = config.ASSISTANT_TEMPLATES_DIR
        self._cached_base_assistant_templates = AssistantService._load_assistant_templates_from_dir(templates_dir)

        admin_templates_dir = config.ASSISTANT_TEMPLATES_DIR / "admin"
        self._cached_admin_assistant_templates = AssistantService._load_assistant_templates_from_dir(
            admin_templates_dir
        )

    def get_assistant_template_by_slug(self, slug: str) -> Assistant | None:
        """
        Retrieves an assistant template by its slug.

        Args:
            slug (str): The slug of the assistant template.

        Returns:
            Assistant: The assistant template with the specified slug, or None if not found.
        """
        if slug in self._cached_base_assistant_templates:
            return self._cached_base_assistant_templates.get(slug)
        elif slug in self._cached_admin_assistant_templates:
            return self._cached_admin_assistant_templates.get(slug)
        else:
            return None

    def get_base_assistant_templates(self) -> List[Assistant]:
        """
        Retrieves all base assistant templates.

        Returns:
            List[Assistant]: A list of all base assistant templates.
        """
        return list(self._cached_base_assistant_templates.values())

    def get_admin_assistant_templates(self) -> List[Assistant]:
        """
        Retrieves all admin assistant templates.

        Returns:
            List[Assistant]: A list of all admin assistant templates.
        """
        return list(self._cached_admin_assistant_templates.values())

    @classmethod
    def _load_assistant_templates_from_dir(cls, templates_dir: Path) -> dict[str, Assistant]:
        """
        Loads assistant templates from the specified directory.

        Args:
            templates_dir (Path): The directory containing the assistant templates.

        Returns:
            dict[str, Assistant]: A dictionary of assistant templates, keyed by their slug.
        """
        assistant_templates = {}
        logger.info(f"Loading assistant templates from {templates_dir}")
        try:
            for filename in os.listdir(templates_dir):
                if filename.endswith(".yaml"):
                    with open(os.path.join(templates_dir, filename), 'r') as file:
                        assistant = Assistant.from_yaml(file.read(), project=CODEMIE_PROJECT_NAME)
                        assistant_templates[assistant.slug] = assistant
        except Exception as e:
            logger.error(f"Failed to load assistant template: {e}")
        return assistant_templates

    @classmethod
    def belongs_to_project(cls, assistant_id: str, project_name: str) -> bool:
        """
        Verify if an assistant with the given ID belongs to the specified project.

        Args:
            assistant_id: The ID of the assistant to verify
            project_name: The name of the project to check against

        Returns:
            bool: True if the assistant belongs to the project, False otherwise
        """
        try:
            assistant = Assistant.find_by_id(assistant_id)

            return assistant and assistant.project == project_name
        except Exception:
            return False


assistant_service = AssistantService()
