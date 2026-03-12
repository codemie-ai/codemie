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
from unittest.mock import MagicMock
from codemie.rest_api.utils.default_applications import (
    create_default_applications,
    DEMO_PROJECT_NAME,
    CODEMIE_PROJECT_NAME,
)


@pytest.fixture
def mock_application(mocker):
    return mocker.patch('codemie.rest_api.utils.default_applications.Application')


def test_create_demo_application_when_not_exists(mock_application):
    mock_application.get_all_by_fields.return_value = []
    demo_application_instance = MagicMock(name=DEMO_PROJECT_NAME)
    codemie_application_instance = MagicMock(name=CODEMIE_PROJECT_NAME)
    mock_application.side_effect = [demo_application_instance, codemie_application_instance]

    create_default_applications()

    mock_application.get_all_by_fields.assert_called()
    demo_application_instance.save.assert_called_once()
    codemie_application_instance.save.assert_called_once()


def test_create_demo_application_when_exists(mock_application):
    # Mock Application.get_all_by_fields to return existing application
    mock_application_instance = MagicMock()
    mock_application_instance.name = DEMO_PROJECT_NAME
    mock_application.get_all_by_fields.return_value = [mock_application_instance]
    mock_application.return_value = mock_application_instance

    create_default_applications()

    mock_application.get_all_by_fields.assert_called()
    mock_application_instance.save.assert_not_called()
