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

"""Tests for preconfigured skills loader."""

import pytest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from codemie.core.models import SYSTEM_USER
from codemie.rest_api.models.skill import Skill, SkillVisibility
from external.deployment_scripts.preconfigured_skills import (
    create_or_update_skill,
    get_preconfigured_skill_id_by_name,
    load_skill_templates,
    manage_preconfigured_skills,
)


@pytest.fixture
def mock_skill():
    """Create a mock skill."""
    skill = MagicMock(spec=Skill)
    skill.id = "test-skill-id"
    skill.name = "meeting-summary-assistant"
    skill.description = "Generate comprehensive meeting summaries"
    skill.content = "# Meeting Summary Assistant\n\nGenerate summaries..."
    skill.visibility = SkillVisibility.PUBLIC
    skill.categories = ["project_management", "business_analysis"]
    skill.project = "codemie"
    skill.created_by = SYSTEM_USER
    skill.created_date = datetime.now(UTC)
    skill.updated_date = None
    return skill


@pytest.fixture
def skill_template():
    """Create a skill template dictionary."""
    return {
        'name': 'meeting-summary-assistant',
        'description': 'Generate comprehensive meeting summaries',
        'content': '# Meeting Summary Assistant\n\nGenerate summaries...',
        'project': 'codemie',
        'visibility': 'public',
        'categories': ['project_management', 'business_analysis', 'documentation'],
    }


class TestGetPreconfiguredSkillIdByName:
    """Tests for get_preconfigured_skill_id_by_name function."""

    def test_get_existing_skill_id(self):
        """Test getting ID of existing skill."""
        from external.deployment_scripts.preconfigured_skills import preconfigured_skill_ids

        preconfigured_skill_ids['test-skill'] = 'test-id-123'

        result = get_preconfigured_skill_id_by_name('test-skill')

        assert result == 'test-id-123'

    def test_get_nonexistent_skill_id(self):
        """Test getting ID of non-existent skill returns None."""
        result = get_preconfigured_skill_id_by_name('nonexistent-skill')

        assert result is None


class TestLoadSkillTemplates:
    """Tests for load_skill_templates function."""

    @patch('external.deployment_scripts.preconfigured_skills.os.listdir')
    @patch('external.deployment_scripts.preconfigured_skills.config.SKILL_TEMPLATES_DIR')
    @patch('builtins.open', create=True)
    def test_load_valid_templates(self, mock_open, mock_templates_dir, mock_listdir):
        """Test loading valid skill templates."""
        mock_templates_dir.exists.return_value = True
        mock_listdir.return_value = ['skill1.yaml', 'skill2.yaml', 'not-a-yaml.txt']

        # Mock file content
        mock_open.return_value.__enter__.return_value.read.side_effect = [
            "name: skill1\ndescription: Test skill 1",
            "name: skill2\ndescription: Test skill 2",
        ]

        templates = load_skill_templates()

        assert len(templates) == 2
        assert templates[0]['name'] == 'skill1'
        assert templates[1]['name'] == 'skill2'

    @patch('external.deployment_scripts.preconfigured_skills.config.SKILL_TEMPLATES_DIR')
    def test_load_templates_dir_not_exists(self, mock_templates_dir):
        """Test loading when directory doesn't exist."""
        mock_templates_dir.exists.return_value = False

        templates = load_skill_templates()

        assert templates == []


class TestCreateOrUpdateSkill:
    """Tests for create_or_update_skill function."""

    @patch('external.deployment_scripts.preconfigured_skills.SkillRepository.get_by_name_author_project')
    @patch('external.deployment_scripts.preconfigured_skills.SkillRepository.update')
    def test_update_existing_skill(self, mock_update, mock_get_by_name, mock_skill, skill_template):
        """Test updating an existing skill."""
        mock_get_by_name.return_value = mock_skill
        mock_update.return_value = mock_skill

        skill_id = create_or_update_skill(skill_template)

        assert skill_id == mock_skill.id
        mock_get_by_name.assert_called_once_with(
            name='meeting-summary-assistant',
            author_id=SYSTEM_USER.id,
            project='codemie',
        )
        mock_update.assert_called_once()

    @patch('external.deployment_scripts.preconfigured_skills.SkillRepository.get_by_name_author_project')
    @patch('external.deployment_scripts.preconfigured_skills.SkillRepository.create')
    def test_create_new_skill(self, mock_create, mock_get_by_name, mock_skill, skill_template):
        """Test creating a new skill."""
        mock_get_by_name.return_value = None
        mock_skill.id = 'new-skill-id'
        mock_create.return_value = mock_skill

        skill_id = create_or_update_skill(skill_template)

        assert skill_id == 'new-skill-id'
        mock_get_by_name.assert_called_once()
        mock_create.assert_called_once()

        # Verify create was called with correct data
        call_args = mock_create.call_args[0][0]
        assert call_args['name'] == 'meeting-summary-assistant'
        assert call_args['created_by'] == SYSTEM_USER
        assert 'project_management' in call_args['categories']

    @patch('external.deployment_scripts.preconfigured_skills.SkillRepository.get_by_name_author_project')
    @patch('external.deployment_scripts.preconfigured_skills.SkillRepository.create')
    def test_create_skill_filters_invalid_categories(self, mock_create, mock_get_by_name, mock_skill):
        """Test that invalid categories are filtered out."""
        mock_get_by_name.return_value = None
        mock_skill.id = 'new-skill-id'
        mock_create.return_value = mock_skill

        template = {
            'name': 'test-skill',
            'description': 'Test',
            'content': 'Content',
            'categories': ['invalid_category', 'project_management', 'another_invalid'],
        }

        create_or_update_skill(template)

        call_args = mock_create.call_args[0][0]
        assert 'project_management' in call_args['categories']
        assert 'invalid_category' not in call_args['categories']
        assert 'another_invalid' not in call_args['categories']


class TestManagePreconfiguredSkills:
    """Tests for manage_preconfigured_skills function."""

    @patch('external.deployment_scripts.preconfigured_skills.create_or_update_skill')
    @patch('external.deployment_scripts.preconfigured_skills.load_skill_templates')
    def test_manage_skills_with_templates(self, mock_load_templates, mock_create_skill, skill_template):
        """Test managing skills with valid templates."""
        mock_load_templates.return_value = [skill_template]
        mock_create_skill.return_value = 'skill-id-123'

        manage_preconfigured_skills()

        mock_load_templates.assert_called_once()
        mock_create_skill.assert_called_once_with(skill_template)

    @patch('external.deployment_scripts.preconfigured_skills.load_skill_templates')
    @patch('external.deployment_scripts.preconfigured_skills.logger')
    def test_manage_skills_no_templates(self, mock_logger, mock_load_templates):
        """Test managing skills when no templates are found."""
        mock_load_templates.return_value = []

        manage_preconfigured_skills()

        mock_load_templates.assert_called_once()
        mock_logger.warning.assert_called_once_with("No skill templates found")

    @patch('external.deployment_scripts.preconfigured_skills.create_or_update_skill')
    @patch('external.deployment_scripts.preconfigured_skills.load_skill_templates')
    def test_manage_skills_with_invalid_template(self, mock_load_templates, mock_create_skill):
        """Test managing skills with template missing name."""
        invalid_template = {'description': 'No name field'}
        mock_load_templates.return_value = [invalid_template]

        manage_preconfigured_skills()

        mock_load_templates.assert_called_once()
        mock_create_skill.assert_not_called()

    @patch('external.deployment_scripts.preconfigured_skills.create_or_update_skill')
    @patch('external.deployment_scripts.preconfigured_skills.load_skill_templates')
    def test_manage_skills_creation_returns_none(self, mock_load_templates, mock_create_skill, skill_template):
        """Test managing skills when creation/update returns None (failure)."""
        mock_load_templates.return_value = [skill_template]
        mock_create_skill.return_value = None  # Indicates failure

        manage_preconfigured_skills()

        mock_load_templates.assert_called_once()
        mock_create_skill.assert_called_once_with(skill_template)
