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
from typing import List, Any
from jinja2 import Template
from codemie.core.utils import format_json_content, format_markdown_content

html_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Summary Analysis</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding: 20px; }
        .accordion-body { font-family: Arial, sans-serif; }
        pre { background-color: #f8f9fa; padding: 10px; border-radius: 5px; }
    </style>
</head>
<body>
<div class="container mt-4">
    <h1 class="text-center mb-4">Summary Analysis Report</h1>
    <div class="accordion" id="accordionMain">
    {% for state in states %}
        <div class="accordion-item">
            <h2 class="accordion-header" id="heading{{ loop.index }}">
                <button class="accordion-button collapsed" type="button"
                        data-bs-toggle="collapse"
                        data-bs-target="#collapse{{ loop.index }}"
                        aria-expanded="false"
                        aria-controls="collapse{{ loop.index }}">
                    {{ state.title }}
                </button>
            </h2>
            <div id="collapse{{ loop.index }}" class="accordion-collapse collapse">
                <div class="accordion-body">
                    {{ state.content | safe }}
                </div>
            </div>
        </div>
    {% endfor %}
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
'''


def generate_html_report(states: List[Any]) -> str:
    state_data = []
    for state in states:
        sanitized_state_name = state.name.replace('/', '_')
        markdown_text = state.output or ""

        try:
            json_content = json.loads(markdown_text)
            html_content = format_json_content(json_content)
        except json.JSONDecodeError:
            html_content = format_markdown_content(markdown_text)

        state_title = f"{sanitized_state_name}_{state.status.value}"
        state_data.append({"title": state_title, "content": html_content})

    html_content = Template(html_template).render(states=state_data)
    return html_content
