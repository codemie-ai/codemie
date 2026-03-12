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

import secrets
import tarfile
import os
import shutil
import json
import time
import threading
import base64
from elasticsearch import helpers
from codemie.rest_api.models.base import BaseModelWithElasticSupport
from codemie.configs import config, logger
from typing import Dict, Any, Iterable, Union, List, Optional


class CodemieExportService(BaseModelWithElasticSupport):
    assistant_id: str
    user: str
    job_id: int
    _index = config.ASSISTANTS_INDEX
    assistant: Dict[str, Any]
    target_project: str = "Demo"
    target_user: str = "dev-codemie-user"

    @classmethod
    def dump(cls):
        assistant = cls.get_by_id(id_=cls.assistant_id)
        cls.save_index_file([assistant])
        cls.assistant = assistant['_source']
        cls.dump_projects(project_name=cls.assistant['project'])
        cls.dump_user_settings(project_name=cls.assistant['project'])
        if 'context' in cls.assistant and cls.assistant['context']:
            for context in cls.assistant['context']:
                name = context['name']
                context_type = context['context_type']
                context_index_name = f"{cls.assistant['project']}-{name}-{context_type}"
                logger.info(f"Dumping context {name} of type {context_type}")
                if context_type == 'code':
                    cls.dump_repositories(context_name=name, project_name=cls.assistant['project'])
                elif context_type == 'knowledge_base':
                    cls.dump_index(index=context['name'])
                if cls._client().indices.exists(index=context_index_name):
                    cls.dump_index(index=context_index_name)
                cls.dump_index_status(context_name=name, project_name=cls.assistant['project'])

    @classmethod
    def dump_projects(cls, project_name: str):
        _index = config.ELASTIC_APPLICATION_INDEX
        query = {"query": {"bool": {"must": [{"match": {"name": project_name}}]}}}
        response = cls._client().search(index=_index, body=query)
        if response['hits']['hits']:
            cls.save_index_file(response['hits']['hits'], project_name=project_name if project_name else None)

    @classmethod
    def dump_index_status(cls, context_name: str, project_name: str):
        _index = "index_status"
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"created_by.id": cls.user}},
                        {"match": {"repo_name": context_name}},
                        {"match": {"project_name": project_name}},
                    ]
                }
            }
        }
        response = cls._client().search(index=_index, body=query)
        if response['hits']['hits']:
            cls.save_index_file(
                response['hits']['hits'],
                context_name=context_name if context_name else None,
                project_name=project_name if project_name else None,
            )

    @classmethod
    def dump_repositories(cls, context_name: str, project_name: str):
        _index = config.ELASTIC_GIT_REPO_INDEX
        query = {"query": {"bool": {"must": [{"match": {"name": context_name}}, {"match": {"app_id": project_name}}]}}}
        response = cls._client().search(index=_index, body=query)
        if response['hits']['hits']:
            cls.save_index_file(
                response['hits']['hits'],
                context_name=context_name if context_name else None,
                project_name=project_name if project_name else None,
            )

    @classmethod
    def dump_user_settings(cls, project_name: str):
        _index = "codemie_user_settings"
        query = {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"setting_type": "user"}},
                        {"match": {"user_id": cls.user}},
                        {"match": {"project_name": project_name}},
                    ]
                }
            }
        }
        response = cls._client().search(index=_index, body=query)
        if response['hits']['hits']:
            cls.save_index_file(response['hits']['hits'], project_name=project_name if project_name else None)

    @classmethod
    def get_by_id(cls, id_: str) -> Dict[str, Any]:
        doc = cls._client().get(index=cls._index.default, id=id_)
        return dict(doc)

    @classmethod
    def dump_index(cls, index: str):
        query = {"query": {"match_all": {}}}
        docs = helpers.scan(cls._client(), query=query, index=index)
        cls.save_index_file(docs)

    @classmethod
    def save_index_file(
        cls,
        docs: Union[List[Dict[str, Any]], Iterable[Dict[str, Any]]],
        context_name: Optional[str] = None,
        project_name: Optional[str] = None,
    ):
        first_doc = docs[0] if isinstance(docs, list) else next(docs)

        if project_name is not None:
            filename = f"{project_name}-{first_doc['_index']}.json"
            if context_name is not None:
                filename = f"{project_name}-{context_name}-{first_doc['_index']}.json"
        else:
            filename = f"{first_doc['_index']}.json"

        with open(f"{cls.tmp_state_dir}/{filename}", 'w') as f:
            for doc in docs:
                f.write(json.dumps(doc))
                f.write('\n')

    @classmethod
    def tar(cls, assistant_id: str, user: str, env=None):
        if env is None:
            env = {}
        cls.assistant_id = assistant_id
        cls.user = user
        source_base = config.CODEMIE_EXPORT_ROOT
        target_base = 'codemie'
        cls.job_id = secrets.randbelow(90000000) + 10000000
        tar_file_path = f'/tmp/{cls.job_id}_codemie.tar'
        cls.tmp_state_dir = f'/tmp/{cls.job_id}_codemie'
        frontend_dir = source_base + "/.." + "/codemie-ui"
        poetry_lock = "poetry.lock"
        pyproject_toml = "pyproject.toml"
        env_file = ".env"
        devbox_export = "src/templates/devbox_export/"
        llm_templates = "llm-templates"

        logger.info(f"Starting export job with id {cls.job_id}")
        os.makedirs(cls.tmp_state_dir)
        cls.dump()

        with tarfile.open(tar_file_path, 'w') as tar:
            tar.add(source_base + "/src", arcname=target_base + "/src")
            tar.add(source_base + "/" + llm_templates, arcname=target_base + "/" + llm_templates) if os.path.exists(
                source_base + "/" + llm_templates
            ) else None
            tar.add(source_base + "/" + poetry_lock, arcname=target_base + "/" + poetry_lock) if os.path.exists(
                source_base + "/" + poetry_lock
            ) else None
            tar.add(source_base + "/" + pyproject_toml, arcname=target_base + "/" + pyproject_toml) if os.path.exists(
                source_base + "/" + pyproject_toml
            ) else None
            tar.add(source_base + "/" + devbox_export, arcname=target_base) if os.path.exists(
                source_base + "/" + devbox_export
            ) else None
            tar.add(cls.tmp_state_dir, arcname=target_base + "/state_import") if os.path.exists(
                cls.tmp_state_dir
            ) else None
            shutil.copyfile(source_base + "/" + env_file, cls.tmp_state_dir + "/" + env_file) if os.path.exists(
                source_base + "/" + env_file
            ) else None
            env_string = ""
            for key in env:
                env_string += f"{key}={env[key]}\n"
            if os.path.exists(f"{cls.tmp_state_dir}/{env_file}"):
                with open(f"{cls.tmp_state_dir}/{env_file}", 'a') as f:
                    f.write(env_string)
                tar.add(f"{cls.tmp_state_dir}/{env_file}", arcname=target_base + "/" + env_file)
            if os.path.exists(frontend_dir):
                logger.debug(f"Adding frontend directory {frontend_dir} to tar file")
                tar.add(frontend_dir, arcname=target_base + "/codemie-ui")

        if '_codemie' in cls.tmp_state_dir:
            shutil.rmtree(cls.tmp_state_dir)

        threading.Thread(target=cls._delete_file_after_delay, args=(tar_file_path, 60)).start()

        return tar_file_path

    @classmethod
    def _delete_file_after_delay(cls, file_path: str, delay: int):
        time.sleep(delay)
        os.remove(file_path)

    @classmethod
    def _is_base64(cls, s):
        try:
            return base64.b64encode(base64.b64decode(s)).decode() == s
        except Exception:
            return False
