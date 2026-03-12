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

from typing import List

from codemie_tools.base.file_object import FileObject

from codemie.service.file_service.file_service import FileService
from codemie.service.llm_service.llm_service import llm_service


class ImageService:
    @classmethod
    def llm_can_process_images(cls, file_inputs: List[str | FileObject], llm_model: str) -> bool:
        """
        Checks if the LLM model can process at least one image from the list of file inputs.

        Args:
            file_inputs: List of file inputs (either encoded URLs or FileObject instances)
            llm_model: The LLM model to check

        Returns:
            True if the model is multimodal and at least one image is present, False otherwise
        """
        # First check if the model is multimodal
        if llm_model not in llm_service.get_multimodal_llms():
            return False

        # Check if there's at least one valid image in the list
        for file_input in file_inputs:
            if isinstance(file_input, str):
                if not (file_input := file_input.strip()):
                    continue
                file_obj = FileObject.from_encoded_url(file_input)
            else:
                file_obj = file_input

            if not file_obj:
                continue

            if file_obj.is_image():
                return True

        return False

    @classmethod
    def filter_base64_images(cls, file_names: List[str]) -> List[dict]:
        """
        Filters the provided file names to return only those that represent images, as dictionaries containing
        base64-encoded content and mime type.

        Args:
            file_names: List of file names to filter and process

        Returns:
            List of dictionaries, each containing 'content' (base64-encoded string) and 'mime_type' of the image file
        """
        base64_images = []

        for file_name in file_names:
            if not file_name:
                continue

            file_obj = FileObject.from_encoded_url(file_name)
            if not file_obj:
                continue

            if file_obj.is_image():
                base64_content = FileService.get_image_base64(file_name)
                base64_images.append({'content': base64_content, 'mime_type': file_obj.mime_type})

        return base64_images
