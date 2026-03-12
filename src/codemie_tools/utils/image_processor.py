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
import logging
from typing import Optional

import cv2
import numpy as np
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

# Configure logger
logger = logging.getLogger(__name__)

# Constants for LLM-based image text extraction
DEFAULT_IMAGE_TEXT_PROMPT = """
Extract all text visible in this image. Format it properly preserving paragraphs,
bullet points, and other structural elements.
"""

MAX_TOKENS = 5000


class ImageProcessor:
    """
    Utility class for processing images and extracting text using LLM-based recognition.
    """

    def __init__(self, chat_model: Optional[BaseChatModel] = None):
        """
        Initialize the ImageProcessor.

        Args:
            chat_model (Optional[BaseChatModel]): The language model to use for image text extraction.
        """
        self.chat_model = chat_model

    def encode_image_base64(self, image_bytes: bytes) -> str:
        """
        Encode image bytes to base64 with proper data URL prefix.

        Args:
            image_bytes: Raw image bytes to encode

        Returns:
            str: Base64 encoded image with data URL prefix
        """
        if not image_bytes:
            return ""

        encoded = base64.b64encode(image_bytes).decode('utf-8')
        return f"data:image/jpeg;base64,{encoded}"

    def extract_text_from_image_bytes(self, image_bytes: bytes, custom_prompt: Optional[str] = None) -> str:
        """
        Extract text from image bytes using LLM vision capabilities.

        Args:
            image_bytes (bytes): Raw image bytes to process
            custom_prompt (Optional[str]): Custom prompt to use for the vision model

        Returns:
            str: Extracted text from the image
        """
        try:
            # Convert to numpy array for OpenCV
            nparr = np.frombuffer(image_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if image is None:
                logger.warning("Failed to decode image bytes")
                return ""

            # Convert OpenCV image to JPEG bytes and encode as base64
            _, buffer = cv2.imencode('.jpg', image)
            jpg_as_text = base64.b64encode(buffer).decode('utf-8')

            return self._extract_text_using_llm(jpg_as_text, custom_prompt)

        except Exception as e:
            logger.error(f"Error processing image bytes: {str(e)}")
            return ""

    def _extract_text_using_llm(self, base64_image: str, custom_prompt: Optional[str] = None) -> str:
        """
        Extract text from a base64-encoded image using LLM vision capabilities.

        Args:
            base64_image (str): The base64-encoded image data
            custom_prompt (Optional[str]): Custom prompt to use instead of default

        Returns:
            str: The extracted text from the image
        """
        if not self.chat_model:
            logger.warning("No chat model provided for image text extraction")
            return ""

        prompt = custom_prompt if custom_prompt is not None else DEFAULT_IMAGE_TEXT_PROMPT

        try:
            # Create the message with image data
            image_message = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                ]
            )

            # Get text from the chat model
            response = self.chat_model.invoke([image_message], max_tokens=MAX_TOKENS)

            return response.content

        except Exception as e:
            logger.error(f"Error extracting text from image using LLM: {str(e)}")
            return ""
