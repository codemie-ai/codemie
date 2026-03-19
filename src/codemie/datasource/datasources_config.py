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
from typing import List, Dict

import yaml
from pydantic import BaseModel, Field

from codemie.configs import config, logger


class ExcludedExtensions(BaseModel):
    common: List[str]
    docs_only: List[str]
    code_only: List[str]

    def get_full_code_exclusions(self) -> List[str]:
        return self.common + self.code_only

    def get_full_docs_exclusions(self) -> List[str]:
        return self.common + self.docs_only


class CodeConfig(BaseModel):
    languages_for_splitting: Dict[str, List[str]]
    chunk_size: int
    tokens_size_limit: int
    chunk_overlap: int
    loader_batch_size: int
    summarization_max_tokens_limit: int
    summarization_tokens_overlap: int
    summarization_batch_size: int
    excluded_extensions: ExcludedExtensions
    extension_to_language: Dict[str, str] = Field(default_factory=dict)

    enable_multiprocessing: bool = False
    processing_timeout: int = -1
    max_subprocesses: int = 1

    def __init__(self, **data):
        super().__init__(**data)
        extension_to_language = {}
        for language, extensions in self.languages_for_splitting.items():
            for extension in extensions:
                extension_to_language[extension] = language
        self.extension_to_language = extension_to_language


class JiraConfig(BaseModel):
    chunk_size: int
    chunk_overlap: int
    loader_batch_size: int


class JSONConfig(BaseModel):
    chunk_size: int
    chunk_overlap: int


class ConfluenceConfig(BaseModel):
    loader_max_pages: int
    loader_pages_per_request: int
    loader_batch_size: int
    loader_timeout: int


class FileConfig(BaseModel):
    chunk_size: int
    chunk_overlap: int


class AzureDevOpsWikiConfig(BaseModel):
    chunk_size: int
    chunk_overlap: int
    loader_batch_size: int


class AzureDevOpsWorkItemConfig(BaseModel):
    chunk_size: int
    chunk_overlap: int
    loader_batch_size: int


class XrayConfig(BaseModel):
    chunk_size: int
    chunk_overlap: int
    loader_batch_size: int


class SharePointConfig(BaseModel):
    loader_batch_size: int
    loader_timeout: int
    chunk_size: int
    chunk_overlap: int
    max_file_size_mb: int
    max_retries: int
    graph_api_version: str
    graph_base_url: str


class LoadersConfig(BaseModel):
    code_loader: CodeConfig
    jira_loader: JiraConfig
    json_loader: JSONConfig
    confluence_loader: ConfluenceConfig
    file_loader: FileConfig
    azure_devops_wiki_loader: AzureDevOpsWikiConfig
    azure_devops_work_item_loader: AzureDevOpsWorkItemConfig
    xray_loader: XrayConfig
    sharepoint_loader: SharePointConfig


class StorageConfig(BaseModel):
    embeddings_max_docs_count: int
    indexing_bulk_max_chunk_bytes: int
    indexing_max_retries: int
    indexing_error_retry_wait_min_seconds: int
    indexing_error_retry_wait_max_seconds: int
    indexing_threads_count: int
    processed_documents_threshold: int  # Max amount of processed documents to store in db


class Config(BaseModel):
    loaders: LoadersConfig
    storage: StorageConfig


def load_config(file_path: str) -> Config:
    with open(file_path, "r") as yaml_file:
        config_dict = yaml.safe_load(yaml_file)
    return Config(**config_dict)


config_file_path = os.path.join(config.DATASOURCES_CONFIG_DIR, "datasources-config.yaml")

# Load the configuration
datasources_config = load_config(config_file_path)

# Accessing configurations
CODE_CONFIG = datasources_config.loaders.code_loader
EXTENSION_TO_LANGUAGE = CODE_CONFIG.extension_to_language
JIRA_CONFIG = datasources_config.loaders.jira_loader
JSON_CONFIG = datasources_config.loaders.json_loader
CONFLUENCE_CONFIG = datasources_config.loaders.confluence_loader
FILE_CONFIG = datasources_config.loaders.file_loader
AZURE_DEVOPS_WIKI_CONFIG = datasources_config.loaders.azure_devops_wiki_loader
AZURE_DEVOPS_WORK_ITEM_CONFIG = datasources_config.loaders.azure_devops_work_item_loader
XRAY_CONFIG = datasources_config.loaders.xray_loader
SHAREPOINT_CONFIG = datasources_config.loaders.sharepoint_loader
STORAGE_CONFIG = datasources_config.storage

logger.info(f"CodeDatasourceConfig instantiated: {CODE_CONFIG}")
logger.info(f"JIRADatasourceConfig instantiated: {JIRA_CONFIG}")
logger.info(f"JSONDatasourceConfig instantiated: {JSON_CONFIG}")
logger.info(f"ConfluenceDatasourceConfig instantiated: {CONFLUENCE_CONFIG}")
logger.info(f"FileDataSourceConfig instantiated: {FILE_CONFIG}")
logger.info(f"AzureDevOpsWikiDatasourceConfig instantiated: {AZURE_DEVOPS_WIKI_CONFIG}")
logger.info(f"AzureDevOpsWorkItemDatasourceConfig instantiated: {AZURE_DEVOPS_WORK_ITEM_CONFIG}")
logger.info(f"XrayDatasourceConfig instantiated: {XRAY_CONFIG}")
logger.info(f"SharePointDatasourceConfig instantiated: {SHAREPOINT_CONFIG}")
logger.info(f"DatasourceStorageConfig instantiated: {STORAGE_CONFIG}")
