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

from unittest.mock import MagicMock

import pytest

from codemie_tools.base.artifact_publishing import PublishContext, PublishResult
from codemie_tools.base.artifact_storage import publish_artifact

CTX = PublishContext(user_id="user-1", conversation_id="conv-1", turn_id="turn-1")


def test_publish_artifact_delegates_to_publisher():
    publisher = MagicMock()
    expected = PublishResult(link="sandbox:/v1/files/ENC", name="report.txt")
    publisher.publish.return_value = expected

    result = publish_artifact(publisher, CTX, "report.txt", content=b"hello")

    assert result is expected
    publisher.publish.assert_called_once_with(CTX, "report.txt", content=b"hello")


@pytest.mark.parametrize("publisher,ctx", [(None, CTX), (MagicMock(), None)])
def test_publish_artifact_without_publisher_is_not_delivered(publisher, ctx):
    result = publish_artifact(publisher, ctx, "out/report.txt", content=b"hello")

    assert not result.ok
    assert result.reason == "`report.txt` was not delivered: storage not configured"
