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

import logging
from typing import Any, Dict, List, Optional

from langchain_core.utils import get_from_dict_or_env
from pydantic import ConfigDict, BaseModel, model_validator

logger = logging.getLogger(__name__)

try:
    import googlemaps
except ImportError as e:
    raise ImportError(
        "Could not import googlemaps python package. "
        "Please, install places dependency group: "
        "`pip install langchain-google-community[places]`"
    ) from e


class GooglePlacesAPIWrapper(BaseModel):
    gplaces_api_key: Optional[str] = None
    google_map_client: Optional[googlemaps.Client] = None
    top_k_results: Optional[int] = None
    model_config = ConfigDict(extra='forbid', arbitrary_types_allowed=True)

    @model_validator(mode="before")
    def setup_environment(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        gplaces_api_key = get_from_dict_or_env(values, "gplaces_api_key", "GPLACES_API_KEY")
        values["gplaces_api_key"] = gplaces_api_key
        if gplaces_api_key:
            values["google_map_client"] = googlemaps.Client(key=gplaces_api_key)
        return values

    def places(self, query: str) -> str:
        client_places = self.google_map_client.places(query) if self.google_map_client else {}
        search_results = client_places.get("results", [])
        num_to_return = len(search_results)

        if num_to_return == 0:
            return "Google Places did not find any places that match the description."

        num_to_return = min(num_to_return, self.top_k_results) if self.top_k_results else num_to_return

        places: List[str] = [
            self.fetch_place_details(result["place_id"])
            for result in search_results[:num_to_return]
            if self.fetch_place_details(result["place_id"]) is not None
        ]

        return "\n".join([f"{i + 1}. {place}" for i, place in enumerate(places)])

    def find_near(self, current_location_query: str, target: str, radius: Optional[int] = 3000) -> str:
        logger.info(f"Google Places API query: {current_location_query}, target: {target}, radius: {radius}")
        if not self.google_map_client:
            return "Google Maps client is not initialized."

        geocode_result = self.google_map_client.geocode(current_location_query)
        if not geocode_result:
            return f"Provided current location {current_location_query} is not found."

        location = geocode_result[0].get('geometry', {}).get('location', {})
        nearby_places = self.google_map_client.places_nearby(location=location, keyword=target, radius=radius)
        return str(nearby_places.get('results', []))

    def fetch_place_details(self, place_id: str) -> Optional[str]:
        if not self.google_map_client:
            logging.error("Google Maps client is not initialized.")
            return None

        try:
            place_details = self.google_map_client.place(place_id)
            formatted_details = self.format_place_details(place_details)
            return formatted_details
        except Exception as e:
            logging.error(f"Error fetching place details for place_id {place_id}: {e}")
            return None

    @staticmethod
    def format_place_details(place_details: Dict[str, Any]) -> Optional[str]:
        result = place_details.get("result", {})
        name = result.get("name", "Unknown")
        address = result.get("formatted_address", "Unknown")
        phone_number = result.get("formatted_phone_number", "Unknown")
        website = result.get("website", "Unknown")
        place_id = result.get("place_id", "Unknown")

        formatted_details = (
            f"{name}\nAddress: {address}\n"
            f"Google place ID: {place_id}\n"
            f"Phone: {phone_number}\nWebsite: {website}\n\n"
        )
        return formatted_details
