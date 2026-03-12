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

import os
import json
from codemie.rest_api.models.base import BaseModelWithElasticSupport
from codemie.configs import config, logger
from elasticsearch import helpers


class StateImportService(BaseModelWithElasticSupport):
    def import_indexes(self):
        imported = f'{config.STATE_IMPORT_DIR}/imported.txt'
        if (
            config.STATE_IMPORT_ENABLED
            and config.STATE_IMPORT_DIR
            and os.path.exists(config.STATE_IMPORT_DIR)
            and not os.path.exists(imported)
        ):
            for filename in os.listdir(config.STATE_IMPORT_DIR):
                if filename.endswith(".json"):
                    with open(f'{config.STATE_IMPORT_DIR}/{filename}', 'r') as f:
                        docs = [json.loads(line) for line in f]
                        actions = [
                            {
                                "_index": filename.replace(".json", ""),
                                "_id": doc["_id"],
                                "_source": doc["_source"],
                            }
                            for doc in docs
                        ]
                        logger.info(f"Importing {filename} index with {len(actions)} documents")
                        helpers.bulk(self._client(), actions)
            try:
                with open(imported, 'w'):
                    pass
            except FileExistsError:
                pass
