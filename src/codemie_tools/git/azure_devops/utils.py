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

import difflib
import re
from typing import Optional

AZURE_DEVOPS_URL_PATTERN = r"https://(?:dev\.azure\.com/([^/]+)|([^.]+)\.visualstudio\.com)(?:/([^/]+))?/_git/([^/]+)"


def extract_old_new_pairs(file_query: str):
    """
    Extracts old and new content pairs from a file query.
    Parameters:
        file_query (str): The file query containing old and new content.
    Returns:
        list of tuples: A list where each tuple contains (old_content, new_content).
    """
    old_pattern = re.compile(r"OLD <<<<\s*(.*?)\s*>>>> OLD", re.DOTALL)  # NOSONAR
    new_pattern = re.compile(r"NEW <<<<\s*(.*?)\s*>>>> NEW", re.DOTALL)  # NOSONAR

    old_contents = old_pattern.findall(file_query)
    new_contents = new_pattern.findall(file_query)

    return list(zip(old_contents, new_contents))


def generate_diff(base_text, target_text, file_path):
    base_lines = base_text.splitlines(keepends=True)
    target_lines = target_text.splitlines(keepends=True)
    diff = difflib.unified_diff(base_lines, target_lines, fromfile=f"a/{file_path}", tofile=f"b/{file_path}")

    return "".join(diff)


def get_repo_id(azure_devops_link: str):
    pattern = r"/_git/([^/?]+)"
    match = re.search(pattern, azure_devops_link)
    if match:
        return match.group(1)
    return None


def parse_azure_devops_url(url: str) -> Optional[dict]:
    match = re.match(AZURE_DEVOPS_URL_PATTERN, url)

    if match:
        modern_org, legacy_org, project, repo = match.groups()
        org = modern_org or legacy_org
        is_legacy = legacy_org is not None
        base_url = f"https://dev.azure.com/{org}" if not is_legacy else f"https://{org}.visualstudio.com"
        return {
            "organization": org,
            "project": project or repo,
            "repository": repo,
            "is_legacy_url": is_legacy,
            "base_url": base_url,
        }
    return None
