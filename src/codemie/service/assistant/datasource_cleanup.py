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


from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Session, and_, select

from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.models.index import IndexInfo


def _same_key_still_exists(project: str, repo_name: str, context_type: ContextType) -> bool:
    with Session(IndexInfo.get_engine()) as session:
        index_types = session.exec(
            select(IndexInfo.index_type).where(
                and_(IndexInfo.project_name == project, IndexInfo.repo_name == repo_name)
            )
        ).all()
    return any(Context.index_info_type_from_index_type(index_type) == context_type for index_type in index_types)


def detach_datasource_from_assistants(datasource: IndexInfo) -> int:
    """Remove a deleted datasource from the context of every assistant in its project. Master row only."""
    context_type = Context.index_info_type(datasource)
    # Bedrock KB names are not unique: another datasource may still answer to the same key
    if _same_key_still_exists(datasource.project_name, datasource.repo_name, context_type):
        return 0

    target = Context(context_type=context_type, name=datasource.repo_name)
    updated = 0
    with Session(Assistant.get_engine()) as session:
        statement = select(Assistant).where(
            and_(
                Assistant.project == datasource.project_name,
                Assistant.context.cast(JSONB).contains(  # type: ignore[attr-defined]
                    [{"name": datasource.repo_name, "context_type": context_type}]
                ),
            )
        )
        for assistant in session.exec(statement).all():
            if assistant.project != datasource.project_name:
                continue
            kept = [ctx for ctx in assistant.context if ctx != target]
            if len(kept) == len(assistant.context):
                continue
            assistant.context = kept
            assistant.update_date = datetime.now()
            session.add(assistant)
            updated += 1
        session.commit()
    return updated
