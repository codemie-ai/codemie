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

import pytest
from unittest.mock import MagicMock, patch

from codemie.core.ability import Ability, Action
from codemie.rest_api.models.index import IndexInfo
from codemie.core.models import CreatedByUser


def test_ability_init():
    user = MagicMock(name="test-user")
    ability = Ability(user=user)

    assert ability.user == user


@pytest.fixture
def mock_index_info():
    return IndexInfo(
        project_name="project",
        repo_name="repo",
        embeddings_model="model",
        current_state=10,
        complete_state=10,
        index_type="code",
        created_by=CreatedByUser(username="test_user", id="123"),
    )


def test_ability_can_creator(mock_index_info):
    user = MagicMock(name="test_user", is_admin=True)
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is True
    assert ability.can(Action.WRITE, mock_index_info) is True
    assert ability.can(Action.DELETE, mock_index_info) is True


def test_ability_can_admin(mock_index_info):
    user = MagicMock(name="admin-user", is_admin=True)
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is True
    assert ability.can(Action.WRITE, mock_index_info) is True
    assert ability.can(Action.DELETE, mock_index_info) is True


def test_ability_can_non_creator(mock_index_info):
    user = MagicMock(name="non-creator-user", is_admin=False)
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is False
    assert ability.can(Action.WRITE, mock_index_info) is False
    assert ability.can(Action.DELETE, mock_index_info) is False


@patch("codemie.rest_api.models.permission.Permission.exists_for", return_value=True)
def test_ability_with_permissions(_mock_exists, mock_index_info):
    user = MagicMock(name="non-creator-user", is_admin=False)
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info, check_resource_permissions=True) is True
    assert ability.can(Action.WRITE, mock_index_info, check_resource_permissions=True) is True
    assert ability.can(Action.DELETE, mock_index_info, check_resource_permissions=True) is True


def test_ability_wo_permissions(mock_index_info):
    user = MagicMock(name="non-creator-user", is_admin=False)
    ability = Ability(user=user)

    assert ability.can(Action.READ, mock_index_info) is False
    assert ability.can(Action.WRITE, mock_index_info) is False
    assert ability.can(Action.DELETE, mock_index_info) is False


def test_ability_list(mock_index_info):
    user = MagicMock(name="test_user", is_admin=True)
    ability = Ability(user=user)

    abilities = ability.list(mock_index_info)

    assert abilities == [Action.READ, Action.WRITE, Action.DELETE]
