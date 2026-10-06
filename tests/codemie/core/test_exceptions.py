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

import pytest

from codemie.core.exceptions import ExtendedHTTPException, InterruptedException, ModelNotAllowedException


def test_model_not_allowed_exception_with_project():
    """ModelNotAllowedException includes model name and project ID."""
    exc = ModelNotAllowedException(
        model_name="gpt-4", project_id="proj-123", details="This project does not permit this model."
    )
    assert exc.code == 400
    assert "gpt-4" in exc.message
    assert "proj-123" in exc.message or "proj-123" in exc.details


def test_model_not_allowed_exception_without_project():
    """ModelNotAllowedException works without project ID."""
    exc = ModelNotAllowedException(model_name="gpt-4", project_id=None, details="Model not found in active models.")
    assert exc.code == 400
    assert "gpt-4" in exc.message
    assert exc.details == "Model not found in active models."


def test_model_not_allowed_exception_default_details():
    """ModelNotAllowedException requires explicit details; raises ValueError if not provided."""
    with pytest.raises(ValueError, match="requires explicit details"):
        ModelNotAllowedException(model_name="claude-3", project_id="proj-456")


def test_init_extended_http_exception():
    exception = ExtendedHTTPException(
        code=400,
        message="Invalid input",
        details="The 'email' field must be a valid email address.",
        help="Please check the format of your email and try again.",
    )

    assert exception.code == 400
    assert exception.message == "Invalid input"
    assert exception.details == "The 'email' field must be a valid email address."
    assert exception.help == "Please check the format of your email and try again."


def test_interrupted_exception_stores_checkpoint_state():
    exc = InterruptedException(
        message="workflow paused",
        interrupted_state="state_b",
        checkpoint_state={"next": ["state_b"], "messages": []},
    )
    assert exc.message == "workflow paused"
    assert exc.interrupted_state == "state_b"
    assert exc.checkpoint_state == {"next": ["state_b"], "messages": []}


def test_interrupted_exception_checkpoint_state_defaults_to_none():
    exc = InterruptedException(message="workflow paused", interrupted_state="state_b")
    assert exc.checkpoint_state is None
