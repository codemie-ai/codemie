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

"""
Preconfigured skills loader for marketplace.

This module loads predefined skill templates from YAML files during application
startup and creates them in the database with SYSTEM_USER as the owner.
Similar to preconfigured assistants, these skills are loaded into the marketplace
and accessible to all users.
"""

import os

import yaml

from codemie.configs import config, logger
from codemie.core.models import SYSTEM_USER
from codemie.repository.skill_repository import SkillRepository
from codemie.rest_api.models.skill import SkillCategory, SkillVisibility
from codemie.rest_api.utils.default_applications import CODEMIE_PROJECT_NAME

# skill_name -> UUID of skills created/updated from templates (for later use if needed)
preconfigured_skill_ids: dict[str, str] = {}


def get_preconfigured_skill_id_by_name(name: str) -> str | None:
    """
    Get the ID of a preconfigured skill by its name.

    Args:
        name: The name of the skill.

    Returns:
        The skill ID or None if not found.
    """
    return preconfigured_skill_ids.get(name)


def load_skill_templates() -> list[dict]:
    """
    Load skill templates from YAML files in the skill templates directory.

    Returns:
        List of skill template dictionaries.
    """
    templates_dir = config.SKILL_TEMPLATES_DIR
    skill_templates = []

    logger.info(f"Loading skill templates from {templates_dir}")

    if not templates_dir.exists():
        logger.warning(f"Skill templates directory does not exist: {templates_dir}")
        return skill_templates

    try:
        for filename in os.listdir(templates_dir):
            if filename.endswith(".yaml"):
                file_path = os.path.join(templates_dir, filename)
                try:
                    with open(file_path) as file:
                        skill_data = yaml.safe_load(file.read())
                        if skill_data and 'name' in skill_data:
                            skill_templates.append(skill_data)
                            logger.info(f"Loaded skill template: {skill_data['name']}")
                        else:
                            logger.warning(f"Invalid skill template in {filename}: missing 'name' field")
                except Exception as e:
                    logger.error(f"Failed to load skill template from {filename}: {e}")
    except Exception as e:
        logger.error(f"Failed to load skill templates from directory: {e}")

    return skill_templates


def create_or_update_skill(template: dict) -> str | None:
    """
    Create a preconfigured skill from template if it doesn't exist, or update if it does.

    Args:
        template: The skill template dictionary from YAML.

    Returns:
        The skill ID if created/updated, None if failed.
    """
    skill_name = template.get('name')
    project = template.get('project', CODEMIE_PROJECT_NAME)

    # Check if skill already exists using repository
    existing_skill = SkillRepository.get_by_name_author_project(
        name=skill_name,
        author_id=SYSTEM_USER.id,
        project=project,
    )

    # Parse categories from template (filter valid ones)
    categories = [cat for cat in template.get('categories', []) if cat in [e.value for e in SkillCategory]]

    if existing_skill:
        logger.info(f"Skill '{skill_name}' already exists, updating...")

        # Prepare updates
        updates = {
            'description': template.get('description', ''),
            'content': template.get('content', ''),
            'visibility': SkillVisibility(template.get('visibility', 'public')),
            'categories': categories,
        }

        # Update using repository
        updated_skill = SkillRepository.update(existing_skill.id, updates)
        if updated_skill:
            logger.info(f"Skill '{skill_name}' updated successfully.")
            return updated_skill.id
        else:
            logger.error(f"Failed to update skill '{skill_name}'")
            return None

    # Create new skill using repository
    skill_data = {
        'name': skill_name,
        'description': template.get('description', ''),
        'content': template.get('content', ''),
        'project': project,
        'visibility': SkillVisibility(template.get('visibility', 'public')),
        'created_by': SYSTEM_USER,
        'categories': categories,
    }

    try:
        new_skill = SkillRepository.create(skill_data)
        logger.info(f"Skill '{skill_name}' created successfully with ID: {new_skill.id}")
        return new_skill.id
    except Exception as e:
        logger.error(f"Failed to create skill '{skill_name}': {e}", exc_info=True)
        return None


def manage_preconfigured_skills() -> None:
    """
    Load and manage preconfigured skills from templates during application startup.

    This function:
    1. Loads skill templates from YAML files
    2. Creates or updates skills in the database via SkillRepository
    3. Sets SYSTEM_USER as the owner
    4. Makes skills PUBLIC (marketplace) by default
    """
    logger.info("Managing preconfigured skills from templates")

    # Load templates from YAML files
    templates = load_skill_templates()

    if not templates:
        logger.warning("No skill templates found")
        return

    # Create or update each skill
    success_count = 0
    for template in templates:
        skill_name = template.get('name')
        if not skill_name:
            logger.warning(f"Skipping template without name: {template}")
            continue

        skill_id = create_or_update_skill(template)
        if skill_id:
            preconfigured_skill_ids[skill_name] = skill_id
            success_count += 1

    logger.info(f"Preconfigured skills management completed. Processed {success_count}/{len(templates)} skills.")
