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
import os

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger, config
from codemie.rest_api.models.index import IndexInfo


def create_index_from_dump(project_name: str, index_name: str) -> IndexInfo:
    """
    Restores an Elasticsearch index from a dump file.

    Args:
        project_name (str): The name of the project.
        index_name (str): Name of the index to restore.

    Returns:
        IndexInfo: The restored IndexInfo object.

    The function:
    1. Connects to Elasticsearch.
    2. Reads the dump file with the index name from INDEX_DUMPS_DIR. Default: config/index-dumps.
    3. Restores index settings.
    4. Restores index mappings.
    5. Restores all documents.
    6. Creates and saves the IndexInfo.

    Raises:
        IOError: If unable to read the dump file.
        elasticsearch.ElasticsearchException: If there is an issue with Elasticsearch operations.
    """

    full_index_name = f"{project_name}-{index_name}"

    dump_file_path = os.path.join(config.INDEX_DUMPS_DIR, f"{full_index_name}.json")

    logger.info(f"Creating datasource {index_name} in project {project_name} from file {dump_file_path}")
    es = ElasticSearchClient.get_client()

    with open(dump_file_path, 'r') as f:
        dump_data = json.load(f)

    logger.debug(f"Restoring {full_index_name} index settings")
    es.indices.create(index=full_index_name, body={'settings': dump_data['settings']}, ignore=400)

    logger.debug(f"Restoring {full_index_name} index mappings")
    es.indices.put_mapping(index=full_index_name, body=dump_data['mapping'])

    logger.debug(f"Restoring {len(dump_data['documents'])} documents for {full_index_name}")
    for doc in dump_data['documents']:
        es.index(index=doc['_index'], id=doc['_id'], body=doc['_source'])

    logger.debug(f"Saving status of the {full_index_name} to {config.INDEX_STATUS_INDEX} index")
    index_info_data = json.loads(dump_data['index_info'])
    index_info = IndexInfo(**index_info_data)
    index_info.repo_name = index_name
    index_info.project_name = project_name
    index_info.save()

    logger.info(f"Datasource {index_name} in project {project_name} restored from {dump_file_path}")

    return index_info
