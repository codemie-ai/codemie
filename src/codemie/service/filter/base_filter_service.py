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

from typing import Dict, Any, Callable, Optional, List
from pydantic import BaseModel

from codemie.configs import logger


class FilterFieldConfig(BaseModel):
    field_name: Any
    filter_compose_func: Callable


class FilterField(BaseModel):
    name: str
    config: FilterFieldConfig


class BaseFilterConfig(BaseModel):
    fields: Optional[List[FilterField]]

    @classmethod
    def from_config(cls, config_dict: Dict[str, Any]):
        fields = []
        for field_name, field_config in config_dict.items():
            fields.append(FilterField(name=field_name, config=FilterFieldConfig(**field_config)))
        return cls(fields=fields)


class BaseFilterData:
    FILTER_CONFIG = {}

    @classmethod
    def add_filters(cls, query: Dict[str, Any], raw_filters: Dict[str, Any], is_admin: bool = False):
        filter_config = BaseFilterConfig.from_config(cls.FILTER_CONFIG)
        filter_query = cls._prepare_filters(filters=filter_config, raw_filters=raw_filters)
        if is_admin:
            return {"bool": {"filter": filter_query}}
        elif "bool" in query:
            if "filter" not in query["bool"]:
                query["bool"]["filter"] = []
            query["bool"]["filter"].extend(filter_query)
        else:
            query = {"bool": {"must": [query], "filter": filter_query}}
        logger.debug(f"Composed query with filters: {query}")
        return query

    @classmethod
    def _prepare_filters(cls, filters: BaseFilterConfig, raw_filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        filter_query = []
        for field in filters.fields:
            field_names = field.config.field_name
            filter_value = raw_filters.get(field.name)
            if filter_value or filter_value is False:
                filter_query.append(field.config.filter_compose_func(field_names, filter_value))

        return filter_query

    @classmethod
    def add_sql_filters(cls, query, model_class, raw_filters: Dict[str, Any], is_admin: bool = False):
        """Base method for adding SQL filters to query"""

        for filter_name, filter_value in raw_filters.items():
            if not (filter_value or filter_value is False):
                continue

            filter_config = cls.FILTER_CONFIG.get(filter_name)
            if not filter_config:
                continue

            field_name = filter_config["field_name"]
            if isinstance(field_name, str) and field_name.endswith('.keyword'):
                field_name = field_name[:-8]  # remove .keyword suffix
            elif isinstance(field_name, list):
                field_name = [f[:-8] if f.endswith('.keyword') else f for f in field_name]

            filter_compose_func = filter_config["filter_compose_func"]

            query = filter_compose_func(
                query=query, model_class=model_class, field_name=field_name, filter_value=filter_value
            )

        return query
