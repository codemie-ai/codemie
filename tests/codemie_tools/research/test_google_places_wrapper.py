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

from unittest.mock import patch, MagicMock

import pytest

from codemie_tools.research.google_places_wrapper import GooglePlacesAPIWrapper


class TestGooglePlacesAPIWrapper:
    @pytest.fixture
    def google_places_wrapper(self):
        return MagicMock()

    def test_fetch_place_details(self, google_places_wrapper):
        with patch.object(google_places_wrapper.google_map_client, 'place', return_value={'result': {}}):
            result = google_places_wrapper.fetch_place_details("test_place_id")
            assert result is not None

    def test_format_place_details(self):
        place_details = {
            "result": {
                "name": "Place Name",
                "formatted_address": "123 Main St",
                "formatted_phone_number": "555-5555",
                "website": "http://example.com",
                "place_id": "place_id",
            }
        }
        result = GooglePlacesAPIWrapper.format_place_details(place_details)
        expected_result = (
            "Place Name\nAddress: 123 Main St\n"
            "Google place ID: place_id\n"
            "Phone: 555-5555\nWebsite: http://example.com\n\n"
        )
        assert result == expected_result
