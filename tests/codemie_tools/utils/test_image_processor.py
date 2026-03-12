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

import base64
from unittest.mock import patch

import numpy as np
import pytest
from langchain_core.messages import AIMessage

from codemie_tools.utils.image_processor import ImageProcessor


def test_init_with_no_chat_model():
    """Test initialization without providing a chat model."""
    processor = ImageProcessor()
    assert processor.chat_model is None


def test_init_with_chat_model(image_processor):
    """Test initialization with chat model."""
    assert image_processor.chat_model is not None


def test_encode_image_base64_with_empty_bytes(image_processor):
    """Test encoding empty image bytes."""
    result = image_processor.encode_image_base64(b"")
    assert result == ""


def test_encode_image_base64_with_valid_bytes(image_processor):
    """Test encoding valid image bytes."""
    test_bytes = b"test image data"
    encoded = base64.b64encode(test_bytes).decode('utf-8')
    expected = f"data:image/jpeg;base64,{encoded}"

    result = image_processor.encode_image_base64(test_bytes)
    assert result == expected


@pytest.mark.parametrize(
    "image_bytes,expected_text",
    [
        (b"test image data", "This is the extracted text"),
    ],
)
@patch('cv2.imdecode')
@patch('cv2.imencode')
def test_extract_text_from_image_bytes_success(
    mock_imencode, mock_imdecode, image_bytes, expected_text, mock_chat_model, image_processor
):
    """Test successful text extraction from image bytes."""
    # Mock the cv2 functions
    mock_image = np.zeros((100, 100, 3), dtype=np.uint8)
    mock_imdecode.return_value = mock_image
    mock_imencode.return_value = (True, b'fake_jpeg_bytes')

    # Set up the mock chat model response
    mock_chat_model.invoke.return_value = AIMessage(content=expected_text)

    # Call the method
    result = image_processor.extract_text_from_image_bytes(image_bytes)

    # Assertions
    assert result == expected_text
    mock_chat_model.invoke.assert_called_once()


@patch('cv2.imdecode')
def test_extract_text_from_image_bytes_decode_failure(mock_imdecode, mock_chat_model, image_processor):
    """Test behavior when image decoding fails."""
    # Mock the cv2 function to return None (decoding failure)
    mock_imdecode.return_value = None

    # Call the method
    image_bytes = b"invalid image data"
    result = image_processor.extract_text_from_image_bytes(image_bytes)

    # Assertions
    assert result == ""
    mock_chat_model.invoke.assert_not_called()


def test_extract_text_from_image_bytes_no_chat_model():
    """Test behavior when no chat model is provided."""
    processor = ImageProcessor()  # No chat model
    result = processor.extract_text_from_image_bytes(b"test image data")
    assert result == ""


@patch('cv2.imdecode')
@patch('cv2.imencode')
def test_extract_text_from_image_bytes_with_custom_prompt(
    mock_imencode, mock_imdecode, mock_chat_model, image_processor
):
    """Test text extraction with a custom prompt."""
    # Mock the cv2 functions
    mock_image = np.zeros((100, 100, 3), dtype=np.uint8)
    mock_imdecode.return_value = mock_image
    mock_imencode.return_value = (True, b'fake_jpeg_bytes')

    # Call the method with a custom prompt
    image_bytes = b"test image data"
    custom_prompt = "Custom prompt for testing"
    image_processor.extract_text_from_image_bytes(image_bytes, custom_prompt=custom_prompt)

    # Get the args from the call to the model
    args, _ = mock_chat_model.invoke.call_args

    # Extract the message content to check if it contains our custom prompt
    message_content = args[0][0].content
    text_part = next((item for item in message_content if item['type'] == 'text'), None)

    # Assertion
    assert text_part['text'] == custom_prompt


@patch('cv2.imdecode')
@patch('cv2.imencode')
def test_model_exception_handling(mock_imencode, mock_imdecode, mock_chat_model, image_processor):
    """Test handling of exceptions thrown by the chat model."""
    # Mock the cv2 functions
    mock_image = np.zeros((100, 100, 3), dtype=np.uint8)
    mock_imdecode.return_value = mock_image
    mock_imencode.return_value = (True, b'fake_jpeg_bytes')

    # Configure mock to raise an exception
    mock_chat_model.invoke.side_effect = Exception("Model error")

    # Call the method
    image_bytes = b"test image data"
    result = image_processor.extract_text_from_image_bytes(image_bytes)

    # Assertions
    assert result == ""
