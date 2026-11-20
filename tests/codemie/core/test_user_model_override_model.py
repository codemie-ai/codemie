# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from uuid import uuid4
from codemie.rest_api.models.user_model_override import UserModelOverride


def test_user_model_override_instantiation():
    """Verify UserModelOverride can be created with required fields."""
    user_id = str(uuid4())
    project_name = "test-project"
    allowed_models = ["gpt-4", "claude-opus"]

    override = UserModelOverride(user_id=user_id, project_name=project_name, allowed_models=allowed_models)

    assert override.user_id == user_id
    assert override.project_name == project_name
    assert override.allowed_models == allowed_models
    assert override.default_model is None
    # Timestamps are populated by the repository on create, not by the model.
    assert override.date is None
    assert override.update_date is None
