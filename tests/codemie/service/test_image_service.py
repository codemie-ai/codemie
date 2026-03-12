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
from codemie.core.constants import ModelTypes

from codemie.service.file_service.image_service import ImageService


@pytest.fixture
def mock_file_object(mocker):
    mock_file = mocker.patch('codemie.repository.base_file_repository.FileObject')
    return mock_file


@pytest.fixture
def mock_file_repo(mocker):
    mock_repo = mocker.patch('codemie.repository.repository_factory.FileRepositoryFactory')
    return mock_repo


@pytest.mark.parametrize(
    "file_names,llm_model,expected",
    [
        (["aW1hZ2UvcG5nX3Rlc3RfdGVzdC1pbWFnZS5wbmc="], "GPT_4.1", True),
        (["aW1hZ2UvcG5nX3Rlc3RfdGVzdC1pbWFnZS5wbmc="], "other_model", False),
        ([], "GPT_4.1", False),
        ([None], "GPT_4.1", False),
    ],
)
def test_can_process_image(mock_file_object, file_names, llm_model, expected):
    if hasattr(ModelTypes, 'GPT_4.1'):
        llm_model = getattr(ModelTypes, 'GPT_4.1').value if llm_model == "GPT_4.1" else llm_model
        mock_file_object.from_encoded_url.return_value = mock_file_object
        mock_file_object.mime_type = "image/png"
        assert ImageService.llm_can_process_images(file_names, llm_model) == expected
    else:
        pytest.skip("ModelTypes does not have attribute 'GPT_4.1'")


def test_filter_base64_images(mocker):
    # Setup mock for FileObject.from_encoded_url
    mock_file_obj1 = mocker.MagicMock(name='file_obj1')
    mock_file_obj1.is_image.return_value = True
    mock_file_obj1.mime_type = 'image/jpeg'

    mock_file_obj2 = mocker.MagicMock(name='file_obj2')
    mock_file_obj2.is_image.return_value = False

    mock_file_obj3 = mocker.MagicMock(name='file_obj3')
    mock_file_obj3.is_image.return_value = True
    mock_file_obj3.mime_type = 'image/png'

    mocker.patch(
        'codemie_tools.base.file_object.FileObject.from_encoded_url',
        side_effect=[mock_file_obj1, mock_file_obj2, mock_file_obj3, None],
    )

    # Setup mock for FileService.get_image_base64
    mocker.patch(
        'codemie.service.file_service.file_service.FileService.get_image_base64',
        side_effect=['base64_image1', 'base64_image3'],
    )

    file_names = ['image1.jpg', 'document.pdf', 'image2.png', '']
    result = ImageService.filter_base64_images(file_names)

    expected_result = [
        {'content': 'base64_image1', 'mime_type': 'image/jpeg'},
        {'content': 'base64_image3', 'mime_type': 'image/png'},
    ]
    assert result == expected_result
    assert len(result) == 2
