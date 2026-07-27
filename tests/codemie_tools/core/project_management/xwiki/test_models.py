# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import pytest
from pydantic import ValidationError

from codemie_tools.base.models import CredentialTypes
from codemie_tools.core.project_management.xwiki.models import (
    CreatePageAttachmentInput,
    CreatePageCommentInput,
    CreatePageInput,
    DeletePageAttachmentInput,
    DeletePageInput,
    GetPageAttachmentInput,
    GetPageCommentInput,
    GetPageInput,
    GetSpaceInput,
    GetWikiInput,
    ListPageAttachmentsInput,
    ListPageChildrenInput,
    ListPageCommentsInput,
    ListPageTagsInput,
    ListPagesInput,
    ListSpacesInput,
    ListWikiPagesInput,
    ListWikiTagsInput,
    ListWikisInput,
    ModifyPageInput,
    ReadPageAttachmentContentInput,
    SearchSpaceInput,
    SearchWikiInput,
    SetPageTagsInput,
    XWikiConfig,
)


class TestXWikiConfig:
    def test_defaults(self):
        config = XWikiConfig()
        assert config.url == ""
        assert config.token == ""
        assert config.username is None
        assert config.use_bearer is False

    def test_credential_type_is_xwiki(self):
        config = XWikiConfig()
        assert config.credential_type == CredentialTypes.XWIKI

    def test_credential_type_is_frozen(self):
        from pydantic import ValidationError as PydanticValidationError

        config = XWikiConfig()
        with pytest.raises((PydanticValidationError, TypeError, AttributeError)):
            config.credential_type = CredentialTypes.CONFLUENCE

    def test_with_all_fields(self):
        config = XWikiConfig(url="https://wiki.example.com", token="tok", username="user", use_bearer=True)
        assert config.url == "https://wiki.example.com"
        assert config.token == "tok"
        assert config.username == "user"
        assert config.use_bearer is True

    def test_bearer_default_false(self):
        config = XWikiConfig(url="https://wiki.example.com", token="tok")
        assert config.use_bearer is False


class TestWikiInputModels:
    def test_list_wikis_defaults(self):
        m = ListWikisInput()
        assert m.number == 50
        assert m.start == 0

    def test_list_wikis_custom(self):
        m = ListWikisInput(number=10, start=5)
        assert m.number == 10
        assert m.start == 5

    def test_get_wiki_default(self):
        m = GetWikiInput()
        assert m.wiki == "xwiki"

    def test_get_wiki_custom(self):
        m = GetWikiInput(wiki="teamwiki")
        assert m.wiki == "teamwiki"


class TestSpaceInputModels:
    def test_list_spaces_defaults(self):
        m = ListSpacesInput()
        assert m.wiki == "xwiki"
        assert m.number == 50
        assert m.start == 0

    def test_get_space_requires_space(self):
        with pytest.raises(ValidationError):
            GetSpaceInput()

    def test_get_space_valid(self):
        m = GetSpaceInput(space="Main.Sandbox")
        assert m.wiki == "xwiki"
        assert m.space == "Main.Sandbox"


class TestPageInputModels:
    def test_list_pages_requires_space(self):
        with pytest.raises(ValidationError):
            ListPagesInput()

    def test_list_pages_defaults(self):
        m = ListPagesInput(space="Main")
        assert m.wiki == "xwiki"
        assert m.number == 50
        assert m.start == 0

    def test_list_wiki_pages_defaults(self):
        m = ListWikiPagesInput()
        assert m.wiki == "xwiki"
        assert m.number == 50
        assert m.start == 0

    def test_list_page_children_requires_space_and_page(self):
        with pytest.raises(ValidationError):
            ListPageChildrenInput()

    def test_list_page_children_valid(self):
        m = ListPageChildrenInput(space="Main", page="WebHome")
        assert m.number == 50
        assert m.start == 0

    def test_get_page_requires_space_and_page(self):
        with pytest.raises(ValidationError):
            GetPageInput()

    def test_get_page_is_markdown_default(self):
        m = GetPageInput(space="Main", page="WebHome")
        assert m.is_markdown is False

    def test_create_page_requires_all_fields(self):
        with pytest.raises(ValidationError):
            CreatePageInput(wiki="xwiki", space="Main")

    def test_create_page_syntax_default(self):
        m = CreatePageInput(space="Main", page="MyPage", title="My Page", content="Hello")
        assert m.syntax == "xwiki/2.1"
        assert m.wiki == "xwiki"

    def test_modify_page_optional_title(self):
        m = ModifyPageInput(space="Main", page="MyPage", content="Updated")
        assert m.title is None
        assert m.syntax == "xwiki/2.1"

    def test_modify_page_with_title(self):
        m = ModifyPageInput(space="Main", page="MyPage", content="Updated", title="New Title")
        assert m.title == "New Title"

    def test_delete_page_requires_space_and_page(self):
        with pytest.raises(ValidationError):
            DeletePageInput()

    def test_delete_page_valid(self):
        m = DeletePageInput(space="Main", page="OldPage")
        assert m.wiki == "xwiki"


class TestTagInputModels:
    def test_list_wiki_tags_default_wiki(self):
        m = ListWikiTagsInput()
        assert m.wiki == "xwiki"

    def test_list_page_tags_requires_space_and_page(self):
        with pytest.raises(ValidationError):
            ListPageTagsInput()

    def test_set_page_tags_requires_tags(self):
        with pytest.raises(ValidationError):
            SetPageTagsInput(space="Main", page="MyPage")

    def test_set_page_tags_valid(self):
        m = SetPageTagsInput(space="Main", page="MyPage", tags=["docs", "api"])
        assert m.tags == ["docs", "api"]

    def test_set_page_tags_empty_list(self):
        m = SetPageTagsInput(space="Main", page="MyPage", tags=[])
        assert m.tags == []


class TestCommentInputModels:
    def test_list_page_comments_defaults(self):
        m = ListPageCommentsInput(space="Main", page="MyPage")
        assert m.number == 20
        assert m.start == 0

    def test_get_page_comment_requires_comment_id(self):
        with pytest.raises(ValidationError):
            GetPageCommentInput(space="Main", page="MyPage")

    def test_get_page_comment_valid(self):
        m = GetPageCommentInput(space="Main", page="MyPage", comment_id=42)
        assert m.comment_id == 42

    def test_create_page_comment_requires_text(self):
        with pytest.raises(ValidationError):
            CreatePageCommentInput(space="Main", page="MyPage")

    def test_create_page_comment_valid(self):
        m = CreatePageCommentInput(space="Main", page="MyPage", text="Great page!")
        assert m.text == "Great page!"


class TestAttachmentInputModels:
    def test_list_page_attachments_requires_space_and_page(self):
        with pytest.raises(ValidationError):
            ListPageAttachmentsInput()

    def test_get_page_attachment_requires_filename(self):
        with pytest.raises(ValidationError):
            GetPageAttachmentInput(space="Main", page="MyPage")

    def test_get_page_attachment_valid(self):
        m = GetPageAttachmentInput(space="Main", page="MyPage", filename="doc.pdf")
        assert m.filename == "doc.pdf"

    def test_read_attachment_content_valid(self):
        m = ReadPageAttachmentContentInput(space="Main", page="MyPage", filename="report.pdf")
        assert m.filename == "report.pdf"
        assert m.wiki == "xwiki"

    def test_create_page_attachment_requires_space_and_page(self):
        with pytest.raises(ValidationError):
            CreatePageAttachmentInput()

    def test_delete_page_attachment_requires_filename(self):
        with pytest.raises(ValidationError):
            DeletePageAttachmentInput(space="Main", page="MyPage")


class TestSearchInputModels:
    def test_search_wiki_requires_query(self):
        with pytest.raises(ValidationError):
            SearchWikiInput()

    def test_search_wiki_defaults(self):
        m = SearchWikiInput(query="hello")
        assert m.wiki == "xwiki"
        assert m.scope == "content"
        assert m.space is None
        assert m.number == 10
        assert m.start == 0
        assert m.is_markdown is False

    def test_search_wiki_with_space_filter(self):
        m = SearchWikiInput(query="hello", space="Main.Sandbox")
        assert m.space == "Main.Sandbox"

    def test_search_space_requires_space_and_query(self):
        with pytest.raises(ValidationError):
            SearchSpaceInput()

    def test_search_space_defaults(self):
        m = SearchSpaceInput(space="Main", query="hello")
        assert m.wiki == "xwiki"
        assert m.number == 10
        assert m.start == 0
        assert m.is_markdown is False


def test_xwiki_url_placeholder_default():
    from codemie_tools.core.project_management.xwiki.models import XWikiConfig

    schema = XWikiConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "URL, e.g. https://wiki.example.com"


def test_xwiki_url_default_url_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {"xwiki": {"url": "https://wiki.company.com"}})
    assert customer_config.get_tool_default("xwiki", "url") == "https://wiki.company.com"


def test_xwiki_use_bearer_default_false():
    from codemie_tools.core.project_management.xwiki.models import XWikiConfig

    config = XWikiConfig()
    assert config.use_bearer is False


def test_xwiki_use_bearer_tool_default_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {"xwiki": {"use_bearer": True}})
    assert customer_config.get_tool_default("xwiki", "use_bearer") is True


def test_url_default_wired_to_tool_default():
    from codemie_tools.core.project_management.xwiki.models import XWikiConfig
    from codemie.configs.customer_config import customer_config

    expected = customer_config.get_tool_default(XWikiConfig.TOOL_NAME, "url") or ""
    assert XWikiConfig.model_fields["url"].default == expected
