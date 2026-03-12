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

from unittest.mock import patch, mock_open

import pytest
from langchain_core.documents import Document

from langchain_community.document_loaders import CSVLoader


@pytest.fixture
def mock_csv_data():
    return '\n'.join(["name, age, gender", "John, 30, M", "Jane, 25, F", "Jack, 35, M", "Jill, 28, F"])


def test_load_row_per_document(mock_csv_data):
    with patch("builtins.open", mock_open(read_data=mock_csv_data)):
        loader = CSVLoader(file_path='data/test.csv', csv_args={"delimiter": ','})

        documents = list(loader.load())

        assert len(documents) == 4

        for i, doc in enumerate(documents):
            assert isinstance(doc, Document)
            assert doc.metadata['source'] == 'data/test.csv'
            assert 'row' in doc.metadata
            assert doc.metadata['row'] == i
