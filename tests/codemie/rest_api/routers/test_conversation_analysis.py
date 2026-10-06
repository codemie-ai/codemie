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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_trigger_analysis_returns_400_when_disabled():
    """T5: trigger endpoint returns 400 (not 503) when CONVERSATION_ANALYSIS_ENABLED=False."""
    from codemie.rest_api.routers.conversation_analysis import trigger_analysis_manually, TriggerAnalysisRequest

    mock_user = MagicMock()
    mock_user.id = "user1"
    mock_user.full_name = "Test User"

    with patch("codemie.rest_api.routers.conversation_analysis.config") as mock_config:
        mock_config.CONVERSATION_ANALYSIS_ENABLED = False

        with pytest.raises(HTTPException) as exc_info:
            await trigger_analysis_manually(request=TriggerAnalysisRequest(), user=mock_user, _=None)

    assert exc_info.value.status_code == 400
