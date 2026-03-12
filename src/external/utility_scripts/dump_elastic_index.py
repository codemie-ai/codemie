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

"""
Script for creating Elasticsearch index dumps.

This script creates a complete backup of an Elasticsearch index, including:
- Index settings
- Index mappings
- All documents (up to 10,000 documents)

The dump is saved as a JSON file containing three main sections:
- 'settings': Index configuration and settings
- 'mapping': Field mappings and data types
- 'documents': Actual index data

Usage:
    poetry run python src/external/utility_scripts/dump_elastic_index.py <project_name> <index_name>

Arguments:
    project_name: Name of the Elasticsearch project.
    index_name: Name of the Elasticsearch index to dump

Example:
     poetry run python src/external/utility_scripts/dump_elastic_index.py demo my_index

Output:
    Creates a JSON file in the INDEX_DUMPS_DIR directory named '<project_name>-<index_name>.json'

Note:
    - Requires proper Elasticsearch connection configuration (same that uses local Codemie)
    - Limited to 10,000 documents per index
    - INDEX_DUMPS_DIR must be configured in the .env file and must exist. Default: 'config/index-dumps'.
"""

import json
import sys
import os

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger, config
from codemie.rest_api.models.index import IndexInfo


def dump_index(project_name: str, index_name: str):
    """
    Creates a complete dump of an Elasticsearch index.

    Args:
        project_name (str): Name of the index project
        index_name (str): Name of the index to dump

    The function:
    1. Connects to Elasticsearch
    2. Retrieves index settings
    3. Retrieves index mappings
    4. Retrieves all documents (up to 10,000)
    5. Saves everything to a JSON file

    Raises:
        elasticsearch.NotFoundError: If index doesn't exist
        IOError: If unable to write to output file
    """

    index_info = IndexInfo.get_by_fields(
        {
            "project_name": project_name,
            "repo_name.keyword": index_name,
        }
    ).model_dump_json()
    full_index_name = f"{project_name}-{index_name}"
    logger.info(f"Starting dump index {full_index_name} (project: {project_name}, index: {index_name})")
    # Connect to Elasticsearch
    es = ElasticSearchClient.get_client()

    settings = es.indices.get_settings(index=full_index_name).get(full_index_name).get('settings').get('index')
    settings_to_exclude = [
        "creation_date",
        "uuid",
        "version",
        "provided_name",
    ]
    filtered_settings = {k: v for k, v in settings.items() if k not in settings_to_exclude}

    mapping = es.indices.get_mapping(index=full_index_name).get(full_index_name).get('mappings')

    result = es.search(
        index=full_index_name,
        body={"query": {"match_all": {}}, "size": 10000},
    )

    # Prepare dump data
    dump_data = {
        'index_info': index_info,
        'settings': {"index": filtered_settings},
        'mapping': mapping,
        'documents': result['hits']['hits'],
    }

    output_file = os.path.join(config.INDEX_DUMPS_DIR, f"{full_index_name}.json")

    # Save to file
    with open(output_file, 'w') as f:
        json.dump(dump_data, f)

    logger.info(f"Index {full_index_name} dumped to {output_file}")


dump_index(sys.argv[1], sys.argv[2])
