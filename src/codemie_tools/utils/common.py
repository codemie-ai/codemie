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

import re


def normalize_filename(filename: str) -> str:
    """
    Normalize a filename by replacing all special characters with underscores.
    Consecutive underscores are replaced with a single underscore.
    Periods are also replaced with underscores.

    Args:
        filename (str): The original filename

    Returns:
        str: Normalized filename with special characters replaced by underscores

    Examples:
        >>> normalize_filename('test (1).csv')
        'test_1_csv'
        >>> normalize_filename('file with..multiple...periods')
        'file_with_multiple_periods'
    """
    # Replace all special characters (non-alphanumeric) with underscores
    normalized = re.sub(r'\W', '_', filename)
    # Replace consecutive underscores with a single one
    normalized = re.sub(r'_+', '_', normalized)
    return normalized
