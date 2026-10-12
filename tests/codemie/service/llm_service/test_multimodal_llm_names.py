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


from unittest.mock import patch

from codemie.configs.llm_config import LLMModel
from codemie.service.llm_service.llm_service import llm_service


def _model(base_name: str, deployment_name: str, multimodal: bool | None) -> LLMModel:
    return LLMModel(base_name=base_name, deployment_name=deployment_name, enabled=True, multimodal=multimodal)


def test_get_multimodal_llm_names_includes_base_and_deployment_names_of_multimodal_models_only():
    models = [
        _model("gpt-4.1", "gpt-4.1-2025-04-14", True),
        _model("text-only", "text-only-2025", False),
        _model("unset", "unset-2025", None),
    ]
    with patch.object(llm_service, "get_all_llm_model_info", return_value=models):
        assert llm_service.get_multimodal_llm_names() == {"gpt-4.1", "gpt-4.1-2025-04-14"}
