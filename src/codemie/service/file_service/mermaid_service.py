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
mermaid_service.py

This module provides the MermaidService class for generating Mermaid diagrams (SVG or PNG)
by sending Mermaid code to a remote rendering service.

Classes:
    MermaidService: Contains methods to interact with a remote Mermaid rendering server.

Dependencies:
    - requests: For making HTTP requests to the rendering server.
    - codemie.configs.logger: For logging errors and information.
    - codemie.configs.config: For accessing configuration values (e.g., MERMAID_SERVER_URL).

Usage:
    MermaidService.draw_mermaid(mermaid_code: str, type: MermaidContentType = MermaidContentType.SVG) -> Optional[bytes]
        Sends Mermaid code to the configured server and returns the rendered diagram as bytes,
        or None if generation fails.
"""

import base64
import requests
from codemie.core.constants import MermaidContentType, MermaidMimeType
from codemie.configs import logger, config


class MermaidDiagramGenerationError(RuntimeError):
    pass


class MermaidService:
    @classmethod
    def draw_mermaid(cls, mermaid_code: str, type: MermaidContentType = MermaidContentType.SVG) -> bytes:
        """
        Generate a Mermaid diagram (SVG or PNG) from Mermaid code using a remote rendering service.
        """
        try:
            response = (
                cls._make_external_request(mermaid_code, type)
                if config.MERMAID_USE_MERMAID_INC
                else cls._make_internal_request(mermaid_code, type)
            )
        except Exception as e:
            logger.error(f'Failed to draw Mermaid diagram: {str(e)}')
            raise RuntimeError(f"Failed to draw Mermaid diagram: {e}")
        return cls._process_response(response, type)

    @staticmethod
    def _process_response(response, type: MermaidContentType) -> bytes:
        if response.status_code != 200:
            logger.error(f'Failed to generate Mermaid diagram: {response.status_code}', extra={'type': type})
            raise MermaidDiagramGenerationError(f"Failed to generate Mermaid diagram: {response.status_code}")
        expected_content_type = MermaidMimeType.SVG if type == MermaidContentType.SVG else MermaidMimeType.PNG
        if expected_content_type not in response.headers.get("Content-Type", ""):
            logger.error(f'Unexpected Content-Type: {response.headers.get("Content-Type")}', extra={'type': type})
            raise ValueError(f"Unexpected Content-Type: {response.headers.get('Content-Type')}")
        return response.content

    @staticmethod
    def _make_internal_request(mermaid_code: str, type: MermaidContentType):
        return requests.post(
            f"{config.MERMAID_SERVER_URL}/generate",
            data=mermaid_code,
            headers={"Content-Type": "text/plain"},
            params={"type": type.value},
            timeout=config.MERMAID_SERVER_TIMEOUT,
        )

    @staticmethod
    def _make_external_request(mermaid_code: str, type: MermaidContentType):
        external_generate_type = type.value if type == MermaidContentType.SVG else 'img'
        extra_parameters = f"?type={type.value}" if type == MermaidContentType.PNG else ""
        mermaid_syntax_encoded = base64.b64encode(mermaid_code.encode("utf8")).decode("ascii")
        mermaid_draw_svg_url = f"https://mermaid.ink/{external_generate_type}"
        return requests.get(f"{mermaid_draw_svg_url}/{mermaid_syntax_encoded}{extra_parameters}")
