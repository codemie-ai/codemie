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

from typing import List, Union

from codemie.rest_api.models.provider import ProviderToolkitConfigParameter, ProviderToolArgument


AUTOFILLED_SCHEMA_PARAM_TYPES: List[
    Union[ProviderToolArgument.ArgType, ProviderToolkitConfigParameter.ParameterType]
] = [ProviderToolkitConfigParameter.ParameterType.UUID]

PROVIDER_INDEX_TYPE = "provider"

AICE_DATSOURCE_IDS_FIELD = "code_analysis_datasource_ids"
