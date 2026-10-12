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

"""Tests for the read-only Exchange tools."""

import json
from unittest.mock import MagicMock, patch

import pytest

from codemie_tools.ms_exchange.errors import FolderNotFoundError, MailboxNotFoundError, MessageNotFoundError
from codemie_tools.ms_exchange.graph_client import GraphExchangeClient
from codemie_tools.ms_exchange.ews_client import EwsExchangeClient
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig
from codemie_tools.ms_exchange.tools import EnrichBodyTool, ReadTool, RetrieveByIdTool, RetrieveFoldersTool

FOLDERS = [{"id": "f1", "name": "Inbox", "total_count": 2, "unread_count": 1}]
MESSAGES = [
    {
        "id": "m1",
        "subject": "s",
        "from": "A <a@example.test>",
        "received": None,
        "is_read": False,
        "has_attachments": False,
    }
]
MESSAGE = {**MESSAGES[0], "to": [], "cc": [], "body": "hello"}


def _oauth():
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.OAUTH_AZURE,
        mailbox="u@example.test",
        oauth_tenant_id="t",
        oauth_client_id="c",
        oauth_client_secret="s",
    )


def _ntlm():
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.NTLM,
        mailbox="u@example.test",
        ews_url="https://ews.example.test/EWS/Exchange.asmx",
        service_account_username="svc",
        service_account_password="pw",
    )


@pytest.fixture(params=["graph", "ews"])
def transport(request):
    """Yield (config, client_class) with the builder patched out."""
    if request.param == "graph":
        with patch("codemie_tools.ms_exchange.graph_client.build_graph_client"):
            yield _oauth(), GraphExchangeClient
    else:
        with patch("codemie_tools.ms_exchange.ews_client.build_ews_client"):
            yield _ntlm(), EwsExchangeClient


def test_retrieve_folders_routes_by_auth_type(transport):
    config, cls = transport
    with patch.object(cls, "list_folders", return_value=FOLDERS) as m:
        out = RetrieveFoldersTool(config=config).execute()
    m.assert_called_once_with()
    assert json.loads(out) == FOLDERS


def test_read_routes_by_auth_type_with_defaults(transport):
    config, cls = transport
    with patch.object(cls, "list_messages", return_value=MESSAGES) as m:
        out = ReadTool(config=config).execute()
    m.assert_called_once_with("Inbox", 0, 10)
    assert json.loads(out) == MESSAGES


def test_read_passes_arguments(transport):
    config, cls = transport
    with patch.object(cls, "list_messages", return_value=[]) as m:
        ReadTool(config=config).execute(folder="Sent", start=5, count=3)
    m.assert_called_once_with("Sent", 5, 3)


def test_retrieve_by_id_routes_by_auth_type(transport):
    config, cls = transport
    with patch.object(cls, "get_message", return_value=MESSAGE) as m:
        out = RetrieveByIdTool(config=config).execute(message_id="m1")
    m.assert_called_once_with("m1")
    assert json.loads(out)["body"] == "hello"


def test_retrieve_by_id_not_found_is_message_not_found(transport):
    config, cls = transport
    with patch.object(cls, "get_message", side_effect=MessageNotFoundError()):
        with pytest.raises(MessageNotFoundError) as exc:
            RetrieveByIdTool(config=config).execute(message_id="nope")
    assert str(exc.value) == MessageNotFoundError.MESSAGE
    assert not isinstance(exc.value, (FolderNotFoundError, MailboxNotFoundError))


def test_read_folder_and_mailbox_not_found_stay_distinct(transport):
    config, cls = transport
    for err in (FolderNotFoundError, MailboxNotFoundError):
        with patch.object(cls, "list_messages", side_effect=err()):
            with pytest.raises(err) as exc:
                ReadTool(config=config).execute(folder="X")
        assert type(exc.value) is err


def test_identical_read_calls_return_identical_output(transport):
    config, cls = transport
    with patch.object(cls, "list_messages", return_value=MESSAGES):
        tool = ReadTool(config=config)
        assert tool.execute(folder="Inbox", start=0, count=10) == tool.execute(folder="Inbox", start=0, count=10)


def test_execute_raises_typed_error_directly():
    with patch("codemie_tools.ms_exchange.tools.get_client") as gc:
        gc.return_value.list_folders.side_effect = FolderNotFoundError()
        with pytest.raises(FolderNotFoundError):
            RetrieveFoldersTool(config=_oauth()).execute()


def test_tools_are_safe():
    for tool in (RetrieveFoldersTool(config=_oauth()), ReadTool(config=_oauth()), RetrieveByIdTool(config=_oauth())):
        assert tool.is_safe({}) is True


ATTS = [
    {"id": "a1", "name": "pic.png", "content_type": "image/png", "size": 3, "is_file": True},
    {"id": "a2", "name": "doc.pdf", "content_type": "application/pdf", "size": 3, "is_file": True},
    {"id": "a3", "name": "fwd", "content_type": "message/rfc822", "size": 0, "is_file": False},
]


def _patch_client(cls):
    return (
        patch.object(cls, "get_text_body", return_value="hello"),
        patch.object(cls, "list_attachments", return_value=ATTS),
        patch.object(cls, "get_attachment_bytes", return_value=b"raw"),
    )


def _run_enrich(config, cls, **kwargs):
    a, b, c = _patch_client(cls)
    with a, b, c as fetch, patch("codemie_tools.ms_exchange.enrichment.PdfProcessor") as pdf:
        pdf.extract_text_as_markdown.return_value = "PDFTEXT"
        tool_kwargs = kwargs.pop("tool_kwargs", {})
        out = EnrichBodyTool(config=config, **tool_kwargs).execute(**kwargs)
    return out, fetch


def test_enrich_body_same_shape_on_both_transports(transport):
    config, cls = transport
    with patch("codemie_tools.ms_exchange.tools.ImageProcessor") as ip:
        ip.return_value.extract_text_from_image_bytes.return_value = "IMGTEXT"
        out, _ = _run_enrich(config, cls, message_id="m1", tool_kwargs={"chat_model": object()})
    assert out == (
        "hello\n\n--- Attachment: pic.png ---\nIMGTEXT\n\n--- Attachment: doc.pdf ---\nPDFTEXT"
        "\n\n[skipped: fwd] attachment is not a file"
    )


def test_enrich_body_neither_model_skips_image_but_extracts_pdf(transport):
    config, cls = transport
    out, _ = _run_enrich(config, cls, message_id="m1")
    assert "[skipped: pic.png] no OCR model provided" in out
    assert "PDFTEXT" in out


def test_enrich_body_ocr_model_builds_model_via_factory(transport):
    config, cls = transport
    built = object()
    factory = MagicMock(return_value=built)
    with patch("codemie_tools.ms_exchange.tools.ImageProcessor") as ip:
        ip.return_value.extract_text_from_image_bytes.return_value = "IMGTEXT"
        out, _ = _run_enrich(
            config,
            cls,
            message_id="m1",
            ocr_model="vision-x",
            tool_kwargs={"chat_model": object(), "ocr_model_factory": factory},
        )
    factory.assert_called_once_with("vision-x")
    ip.assert_called_once_with(chat_model=built)
    assert "IMGTEXT" in out and "PDFTEXT" in out


def test_enrich_body_injected_chat_model_used_when_ocr_model_unset(transport):
    config, cls = transport
    chat = object()
    with patch("codemie_tools.ms_exchange.tools.ImageProcessor") as ip:
        ip.return_value.extract_text_from_image_bytes.return_value = "IMGTEXT"
        _run_enrich(config, cls, message_id="m1", tool_kwargs={"chat_model": chat})
    ip.assert_called_once_with(chat_model=chat)


def test_enrich_body_does_not_fetch_non_file_attachment(transport):
    config, cls = transport
    with patch("codemie_tools.ms_exchange.tools.ImageProcessor") as ip:
        ip.return_value.extract_text_from_image_bytes.return_value = "IMGTEXT"
        _, fetch = _run_enrich(config, cls, message_id="m1", tool_kwargs={"chat_model": object()})
    assert [c.args for c in fetch.call_args_list] == [("m1", "a1"), ("m1", "a2")]


def test_enrich_body_without_ocr_model_does_not_fetch_images(transport):
    config, cls = transport
    _, fetch = _run_enrich(config, cls, message_id="m1")
    assert [c.args for c in fetch.call_args_list] == [("m1", "a2")]


def test_enrich_body_fetch_error_reuses_taxonomy(transport):
    config, cls = transport
    with patch.object(cls, "get_text_body", side_effect=MessageNotFoundError()):
        with pytest.raises(MessageNotFoundError):
            EnrichBodyTool(config=config).execute(message_id="x")


def test_enrich_body_schema_and_description_document_ocr_model():
    assert "ocr_model" in EnrichBodyTool.model_fields["args_schema"].default.model_fields
    assert "ocr_model" in EnrichBodyTool.model_fields["description"].default
    assert EnrichBodyTool(config=_oauth()).is_safe({}) is True


def test_enrich_body_oversized_declared_attachment_is_not_fetched(transport):
    config, cls = transport
    big = [{"id": "z1", "name": "big.pdf", "content_type": "application/pdf", "size": 10**9, "is_file": True}]
    with (
        patch.object(cls, "get_text_body", return_value="hello"),
        patch.object(cls, "list_attachments", return_value=big),
        patch.object(cls, "get_attachment_bytes", return_value=b"raw") as fetch,
    ):
        out = EnrichBodyTool(config=config).execute(message_id="m1")
    fetch.assert_not_called()
    assert out == "hello\n\n[skipped: big.pdf] attachment too large"


def test_enrich_body_factory_failure_is_sanitised_and_unchained(transport):
    from codemie_tools.ms_exchange.errors import OcrModelUnavailableError

    config, cls = transport
    factory = MagicMock(side_effect=RuntimeError("secret-provider-text"))
    with pytest.raises(OcrModelUnavailableError) as info:
        _run_enrich(config, cls, message_id="m1", ocr_model="evil", tool_kwargs={"ocr_model_factory": factory})
    assert info.value.__cause__ is None and info.value.__context__ is None
    assert "secret-provider-text" not in str(info.value)
    assert "Omit ocr_model" in str(info.value)
