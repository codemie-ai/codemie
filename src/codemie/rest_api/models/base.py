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
from enum import Enum, StrEnum
from typing import Optional, Dict, Any, Generic, TypeVar, Type, List, get_origin, Union, get_args, Self
from uuid import uuid4

from elasticsearch import Elasticsearch, NotFoundError
from pydantic import BaseModel

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.rest_api.models.standard import PostResponse

from sqlmodel import SQLModel, Session, select, Field
from codemie.clients.postgres import PostgresClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.mutable import MutableList


class CommonBaseModel(SQLModel):
    """Base model containing common fields without any database-specific methods"""

    id: Optional[str] = Field(default=None, primary_key=True)
    date: Optional[datetime] = None
    update_date: Optional[datetime] = Field(default=None)

    def __init__(self, **data):
        # Trick to make SQLModel properly validate Pydantic model
        # https://github.com/fastapi/sqlmodel/issues/453
        if self.model_config.get("table", False):
            # Clean null bytes from string values
            cleaned_data = _remove_null_bytes(data)
            self.model_config["table"] = False
            super().__init__(**cleaned_data)
            self.model_config["table"] = True
        else:
            super().__init__(**data)

    @classmethod
    def get_by_fields(cls: Type[T], fields: Dict[str, Any]) -> Optional[T]:
        """Get a single entity by matching fields

        Args:
            fields: Dictionary of field names and their values to match

        Returns:
            Optional[CommonBaseModel]: The matching entity or None if not found
        """
        raise NotImplementedError("Subclasses must implement get_by_fields")

    @classmethod
    def get_all_by_fields(cls: Type[T], fields: Dict[str, Any]) -> List[T]:
        raise NotImplementedError("Subclasses must implement get_all_by_fields")

    def validate_fields(self) -> str:
        return ""


class BaseModelWithElasticSupport(CommonBaseModel):
    _elastic_client: Optional[Elasticsearch] = None
    _index: str

    @classmethod
    def refresh(cls):
        """Refreshes the index. Warning: This is a heavy operation in time!"""
        cls._client().indices.refresh(index=cls._index.default)

    @classmethod
    def as_objects(cls, result: list, klass: Optional[BaseModel] = None) -> list:
        """Convert the result of a search query to a list of objects"""
        if klass:
            return [klass(**hit["_source"]) for hit in result["hits"]["hits"]]

        return [cls(**hit["_source"]) for hit in result["hits"]["hits"]]

    @classmethod
    def run_query(
        cls, query: dict, sort: dict = None, page_size: int = 10_000, start_index: int = 0, source: list[str] = None
    ):
        if sort is None:
            sort = {"date": "DESC"}

        if source is None:
            source = list(cls.model_fields.keys())

        return cls._client().search(
            index=cls._index.default,
            body={"query": query, "size": page_size, "from": start_index, "sort": sort, "_source": source},
        )

    @property
    def elastic_client(self) -> Elasticsearch:
        if not isinstance(self._elastic_client, Elasticsearch):
            self._elastic_client = ElasticSearchClient.get_client()
        return self._elastic_client

    @classmethod
    def _client(cls) -> Elasticsearch:
        # singleton
        if not isinstance(cls._elastic_client, Elasticsearch):
            cls._elastic_client = ElasticSearchClient.get_client()
        return cls._elastic_client

    @classmethod
    def get_by_id(cls, id_: str) -> BaseModelWithElasticSupport:
        res = cls._client().get(index=cls._index.default, id=id_)
        return cls(**res["_source"])

    @classmethod
    def find_by_id(cls, id_: str) -> BaseModelWithElasticSupport | None:
        try:
            return cls.get_by_id(id_)
        except (KeyError, IndexError, NotFoundError):
            return None

    @classmethod
    def get_by_ids(cls, ids: list[str]) -> list[BaseModelWithElasticSupport]:
        body = {"query": {"ids": {"values": ids}}}
        res = cls._client().search(index=cls._index.default, body=body)
        return [cls(**hit["_source"]) for hit in res["hits"]["hits"]]

    @classmethod
    def get_all(
        cls, response_class: BaseModel = None, page_number: int = 1, items_per_page: int = 10_000
    ) -> list[BaseModelWithElasticSupport]:
        body = {"query": {"match_all": {}}}

        if response_class:
            body['source'] = list(response_class.model_fields.keys())

        per_page = min(page_number * items_per_page, 10_000)

        res = cls._client().search(
            index=cls._index.default,
            body=body,
            size=per_page,
            from_=(page_number - 1) * items_per_page,
            sort=[{"update_date": {"order": "desc", "unmapped_type": "date"}}],
        )

        return [cls(**hit["_source"]) for hit in res["hits"]["hits"]]

    @classmethod
    def get_by_fields(cls, fields: Dict[str, Any]) -> BaseModelWithElasticSupport | None:
        conditions = [{"match": {k: v}} for k, v in fields.items()]
        query = {"query": {"bool": {"must": conditions}}}

        res = cls._client().search(index=cls._index.default, body=query)
        if res["hits"]["hits"]:
            return cls(**res["hits"]["hits"][0]["_source"])
        else:
            return None

    @classmethod
    def get_all_by_fields(cls, fields: Dict[str, Any]) -> list[BaseModelWithElasticSupport]:
        conditions = [{"match": {k: v}} for k, v in fields.items()]
        query = {"query": {"bool": {"must": conditions}}}

        res = cls._client().search(index=cls._index.default, body=query, size=10_000)

        return [cls(**hit["_source"]) for hit in res["hits"]["hits"]]

    @classmethod
    def get_all_by_term_fields(cls, fields: Dict[str, Any]) -> list[BaseModelWithElasticSupport]:
        conditions = [{"term": {k: v}} for k, v in fields.items()]
        query = {"query": {"bool": {"must": conditions}}}

        res = cls._client().search(index=cls._index.default, body=query, size=10_000)

        return [cls(**hit["_source"]) for hit in res["hits"]["hits"]]

    def save(self, refresh=False, validate=True) -> PostResponse:
        if not self.id:
            self.id = str(uuid4())
        if not self.date:
            self.date = datetime.now()
            self.update_date = self.date

        if validate:
            validation_message = self.validate_fields()
            if validation_message:
                raise ValueError(validation_message)

        # Check why?
        response = self.elastic_client.index(
            index=self._index,
            id=self.id,
            document=self.model_dump(),
            refresh=refresh,  # Refresh the index immediately after the operation
        )
        return PostResponse(id=response["_id"])

    def update(self, refresh=False, validate=True):
        self.update_date = datetime.now()
        if validate:
            validation_message = self.validate_fields()
            if validation_message:
                raise ValueError(validation_message)
        response = self.elastic_client.update(
            index=self._index,
            id=self.id,
            doc=self.model_dump(),
            retry_on_conflict=10,
            refresh=refresh,  # Refresh the index immediately after the operation
        )
        return PostResponse(id=response["_id"])

    def delete(self):
        res = self._client().delete(index=self._index, id=self.id)
        return res


T = TypeVar('T', bound=BaseModel)


def _remove_null_bytes(data: Any) -> Any:
    """Recursively remove NULL bytes from strings"""
    if isinstance(data, dict):
        return {k: _remove_null_bytes(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_remove_null_bytes(item) for item in data]
    elif isinstance(data, str):
        return data.replace('\x00', '')
    return data


class PydanticType(TypeDecorator[T]):
    """
    A custom SQLAlchemy type that handles conversion between JSONB database storage
    and Pydantic model instances.

    This type is needed because SQLAlchemy's JSONB type only handles basic Python types
    (dict, list, etc.), but doesn't know how to convert complex nested objects into
    their corresponding Pydantic models. This type automatically handles the conversion
    in both directions:
    - When loading from DB: converts JSONB dict → Pydantic object
    - When saving to DB: converts Pydantic object → JSONB dict

    Example:
        class User(BaseModel):
            id: str
            name: str

        class Document(SQLModel, table=True):
            id: str
            created_by: Optional[User] = Field(
                sa_column=Column('created_by', PydanticType(User))
            )

        # Now you can work with created_by as a proper User object:
        doc = session.get(Document, "123")
        print(doc.created_by.name)  # Full type hints and validation

    Args:
        pydantic_type (Type[T]): The Pydantic model class to convert to/from
    """

    impl = JSONB
    cache_ok = True

    def __init__(self, pydantic_type: Type[T]):
        super().__init__()
        self.pydantic_type = pydantic_type

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return self.pydantic_type.model_validate(value)

    def process_bind_param(self, value, dialect):
        """Remove NULL bytes when saving to database"""
        if value is None:
            return None
        if not isinstance(value, BaseModel):
            return value
        data = value.model_dump()
        return _remove_null_bytes(data)


class PydanticListType(TypeDecorator[List[T]]):
    """
    A custom SQLAlchemy type that handles conversion between JSONB array storage
    and lists of Pydantic model instances.

    Similar to PydanticType, but handles lists of objects. This is useful when
    you need to store arrays of complex objects in the database while working
    with them as proper typed objects in your code.

    This type automatically handles:
    - Loading from DB: converts list of dicts → list of Pydantic objects
    - Saving to DB: converts list of Pydantic objects → list of dicts

    Example:
        class Comment(BaseModel):
            text: str
            author: str
            date: datetime

        class Post(SQLModel, table=True):
            id: str
            comments: List[Comment] = Field(
                default_factory=list,
                sa_column=Column('comments', PydanticListType(Comment))
            )

        # Now you can work with comments as a list of Comment objects:
        post = session.get(Post, "123")
        for comment in post.comments:
            print(f"{comment.author}: {comment.text}")  # Full type hints and validation

    Args:
        pydantic_type (Type[T]): The Pydantic model class for list items
    """

    impl = JSONB
    cache_ok = True

    def __init__(self, pydantic_type: Type[T]):
        super().__init__()
        self.pydantic_type = pydantic_type

    def process_result_value(self, value, dialect):
        """Convert JSON array from DB into list of Pydantic objects"""
        if value is None:
            return []
        result = []
        for item in value:
            # Handle backward compatibility - if item is a string or not a dict, skip it
            if isinstance(item, str):
                continue
            if not isinstance(item, dict):
                continue
            try:
                result.append(self.pydantic_type.model_validate(item))
            except Exception:
                # Skip invalid items to handle migration from old data formats
                continue
        return result

    def process_bind_param(self, value, dialect):
        """Remove NULL bytes when saving to database"""
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        if not all(isinstance(item, BaseModel) for item in value):
            return value
        data = [item.model_dump() for item in value]
        return _remove_null_bytes(data)


MutableList.associate_with(PydanticListType)


class BaseModelWithSQLSupport(CommonBaseModel):
    """Base class for SQL database models with common CRUD operations"""

    @classmethod
    def get_engine(cls):
        return PostgresClient.get_engine()

    @classmethod
    def get_by_id(cls, id_: str) -> BaseModelWithSQLSupport:
        with Session(cls.get_engine()) as session:
            result = session.get(cls, id_)
            if not result:
                raise KeyError(f"No {cls.__tablename__} found with id {id_}")
            return result

    @classmethod
    def find_by_id(cls, id_: str) -> Optional[Self]:
        try:
            return cls.get_by_id(id_)
        except KeyError:
            return None

    @classmethod
    def get_by_ids(cls, ids: List[str]) -> List[BaseModelWithSQLSupport]:
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(cls.id.in_(ids))
            return session.exec(statement).all()

    @classmethod
    def get_all(
        cls, response_class: Type[BaseModel] | None = None, page_number: int = 1, items_per_page: int = 10_000
    ) -> List[BaseModelWithSQLSupport]:
        with Session(cls.get_engine()) as session:
            if response_class:
                # Select only columns defined in response_class
                columns = [getattr(cls, field) for field in response_class.model_fields]
                statement = select(*columns)
            else:
                statement = select(cls)

            # Apply pagination
            statement = statement.offset((page_number - 1) * items_per_page)
            statement = statement.limit(items_per_page)
            statement = statement.order_by(cls.update_date.desc())

            results = session.exec(statement).all()

            return results

    @classmethod
    def _get_list_condition(cls, field_name: str, value: Any):
        """
        Check if field is a List type and return appropriate WHERE condition if it is.
        This method emulates Elasticsearch's match behavior for list fields in PostgreSQL.

        In Elasticsearch, when matching against a list field, it returns true if ANY element
        in the list matches the condition. For example:
        {"match": {"credential_values.key": "url"}}
        will match a document if ANY element in credential_values array has key="url"

        This method creates equivalent PostgreSQL condition using contains (@>) operator:
        credential_values @> [{"key": "url"}]

        Args:
            field_name: Field name (without .keyword suffix)
            value: Value to search for

        Returns:
            SQLAlchemy where clause if field is a List type, None otherwise
        """
        parts = field_name.split('.')
        field_type = cls.model_fields[parts[0]].annotation

        # Check if it's a List or Optional[List]
        origin = get_origin(field_type)
        is_list = False
        if origin is list:
            is_list = True
        elif origin is Union:
            args = get_args(field_type)
            is_list = any(get_origin(arg) is list for arg in args)

        if not is_list:
            return None

        # Start with the value and build nested structure backwards
        result = value
        for part in reversed(parts[1:]):
            result = {part: result}

        return getattr(cls, parts[0]).contains([result])

    @classmethod
    def get_by_fields(cls, fields: Dict[str, Any]) -> Optional[BaseModelWithSQLSupport]:
        with Session(cls.get_engine()) as session:
            statement = select(cls)
            for key, value in fields.items():
                # Remove .keyword suffix for PostgreSQL
                key = key.replace('.keyword', '')

                list_condition = cls._get_list_condition(key, value)
                if list_condition is not None:
                    statement = statement.where(list_condition)
                else:
                    statement = statement.where(cls.get_field_expression(key) == value)
            return session.exec(statement).first()

    @classmethod
    def get_all_by_fields(cls, fields: Dict[str, Any]) -> List["BaseModelWithSQLSupport"]:
        with Session(cls.get_engine()) as session:
            statement = select(cls)
            for key, value in fields.items():
                # Remove .keyword suffix for PostgreSQL
                key = key.replace('.keyword', '')
                list_condition = cls._get_list_condition(key, value)
                if list_condition is not None:
                    statement = statement.where(list_condition)
                else:
                    statement = statement.where(cls.get_field_expression(key) == value)
            return session.exec(statement).all()

    @classmethod
    def get_all_by_term_fields(cls, fields: Dict[str, Any]) -> List[BaseModelWithSQLSupport]:
        return cls.get_all_by_fields(fields)

    def save(self, refresh=False, validate=True) -> PostResponse:
        if not self.id:
            self.id = str(uuid4())
        if not self.date:
            self.date = datetime.now()
            self.update_date = self.date

        if validate:
            validation_message = self.validate_fields()
            if validation_message:
                raise ValueError(validation_message)

        with Session(self.get_engine()) as session:
            session.add(self)
            session.commit()
            session.refresh(self)
        return PostResponse(id=str(self.id))

    def update(self, refresh=False, validate=True):
        self.update_date = datetime.now()
        if validate:
            validation_message = self.validate_fields()
            if validation_message:
                raise ValueError(validation_message)

        with Session(self.get_engine()) as session:
            session.merge(self)
            session.commit()
        return PostResponse(id=self.id)

    def refresh(self) -> None:
        with Session(self.get_engine()) as session:
            session.add(self)
            session.refresh(self)

    def delete(self):
        with Session(self.get_engine()) as session:
            session.delete(self)
            session.commit()
        return {"status": "deleted"}

    @classmethod
    def get_field_expression(cls, field_path: str):
        """
        Convert field path to SQLAlchemy expression, handling nested JSON paths
        Examples:
        - 'name' -> Assistant.name
        - 'created_by.name' -> Assistant.created_by['name'].astext
        - 'created_by.user.name' -> Assistant.created_by['user']['name'].astext
        """
        parts = field_path.split('.')
        if len(parts) == 1:
            return getattr(cls, field_path)

        # Handle nested JSON path
        base_field = getattr(cls, parts[0])
        for part in parts[1:-1]:  # Process all intermediate parts
            base_field = base_field[part]
        # Add .astext only to the final part
        return base_field[parts[-1]].astext


PaginatedModelType = TypeVar('PaginatedModelType')


class PaginationData(BaseModel):
    page: int
    per_page: int
    total: int
    pages: int


class PaginatedListResponse(BaseModel, Generic[PaginatedModelType]):
    data: list[PaginatedModelType]
    pagination: PaginationData


class ConversationStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    INTERRUPTED = "interrupted"


class CamelCaseStrEnum(StrEnum):
    """Enum that returns the name in CamelCase"""

    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return CamelCaseStrEnum.camel_case(name)

    @staticmethod
    def camel_case(snake_str):
        components = snake_str.lower().split('_')
        return ''.join(x.title() for x in components)
