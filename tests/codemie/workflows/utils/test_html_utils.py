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

import json
import pytest
from typing import List
from datetime import datetime
from bs4 import BeautifulSoup

from codemie.workflows.utils.html_utils import generate_html_report
from codemie.core.workflow_models import WorkflowExecutionState, WorkflowExecutionStatusEnum


@pytest.fixture
def sample_states() -> List[WorkflowExecutionState]:
    return [
        WorkflowExecutionState(
            execution_id="a090e12f-69e8-4cdd-8626-16454069b65c",
            name="State 1",
            output='{"key": "value"}',
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            completed_at=datetime(2021, 1, 1, 0, 0, 0),
        ),
        WorkflowExecutionState(
            execution_id="a090e12f-69e8-4cdd-8626-16454069b65c",
            name="State 2",
            output="This is a markdown content.",
            status=WorkflowExecutionStatusEnum.SUCCEEDED,
            completed_at=datetime(2021, 1, 2, 0, 0, 0),
        ),
    ]


@pytest.mark.parametrize(
    "state_index,expected_output",
    [(0, '{"key": "value"}'), (1, "This is a markdown content.")],
    ids=("json output", "md output"),
)
def test_generate_html_report(
    sample_states: List[WorkflowExecutionState], state_index: int, expected_output: str
) -> None:
    report_html = generate_html_report(sample_states)

    soup = BeautifulSoup(report_html, 'html.parser')

    assert soup.find('div', class_='container') is not None
    assert soup.find('h1', class_='text-center').text.strip() == "Summary Analysis Report"
    assert len(soup.find_all('div', class_='accordion-item')) == len(sample_states)

    accordion_button = soup.find_all('button', class_='accordion-button')[state_index]
    assert sample_states[state_index].name in accordion_button.text
    accordion_body = soup.find_all('div', class_='accordion-body')[state_index]

    if expected_output.startswith('{'):
        assert json.loads(accordion_body.text.strip()) == json.loads(expected_output)
    else:
        assert expected_output in accordion_body.decode_contents().strip()
