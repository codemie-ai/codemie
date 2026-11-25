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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

from codemie_tools.base.generated_files import GeneratedFileInfo
from codemie_tools.core.project_management.confluence.tools import GenericConfluenceTool

ATTACH_URL = "/rest/api/content/123/child/attachment"


class FakeSource:
    def __init__(self, files):
        # files: list of (info, bytes)
        self._files = files

    def list_generated(self):
        return [i for i, _ in self._files]

    def read_generated(self, info):
        return next(b for i, b in self._files if i == info)


def gen(name, data=b'{"a": 1}', updated=None):
    return GeneratedFileInfo(name=name, size=len(data), updated=updated), data


def ws(path, data=b'{"a": 1}', blob=None, checksum=None):
    name = path.rsplit("/", 1)[-1]
    return GeneratedFileInfo(name=name, size=len(data), path=path, blob=blob, checksum=checksum), data


def upload(name, data=b"uploaded", owner="o"):
    return SimpleNamespace(name=name, mime_type="text/plain", owner=owner, bytes_content=lambda: data)


@pytest.fixture
def client():
    with (
        patch('codemie_tools.core.project_management.confluence.tools.Confluence') as cls,
        patch('codemie_tools.core.project_management.confluence.tools.validate_creds'),
    ):
        instance = MagicMock()
        instance.attach_content.side_effect = lambda **kw: {"id": "att1", "title": kw["name"]}
        resp = MagicMock(status_code=200, reason="OK", text="{}")
        instance.request.return_value = resp
        cls.return_value = instance
        yield instance


def make_tool(config, files=None, uploads=None, source=True):
    if uploads:
        config.input_files = uploads
    src = FakeSource(files or []) if source else None
    return GenericConfluenceTool(config=config, generated_files=src)


def test_generated_json_attached_with_same_name_and_bytes(client, confluence_config):
    tool = make_tool(confluence_config, [gen("report.json")])
    out = tool.execute("POST", ATTACH_URL, {"file": "report.json"})
    assert "Successfully attached file 'report.json'" in out
    kw = client.attach_content.call_args.kwargs
    assert kw["name"] == "report.json" and kw["content"] == b'{"a": 1}' and kw["page_id"] == "123"
    assert kw["content_type"] == "application/json"
    client.request.assert_not_called()


def test_other_file_selected_by_name(client, confluence_config):
    tool = make_tool(confluence_config, [gen("old.json"), gen("new.json")])
    tool.execute("POST", ATTACH_URL, {"file": "old.json"})
    assert client.attach_content.call_args.kwargs["name"] == "old.json"


def test_clash_refused_listing_both(client, confluence_config):
    tool = make_tool(confluence_config, [gen("r.json")], uploads=[upload("r.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "r.json"})
    assert "uploaded" in str(exc.value) and "generated" in str(exc.value)
    client.attach_content.assert_not_called()


def test_not_found_lists_names(client, confluence_config):
    tool = make_tool(confluence_config, [gen("a.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "nope.json"})
    assert "a.json" in str(exc.value)
    client.attach_content.assert_not_called()


def test_no_file_param_errors_with_available_names(client, confluence_config):
    tool = make_tool(confluence_config, [gen("a.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"title": "x"})
    assert "a.json" in str(exc.value)
    client.attach_content.assert_not_called()
    client.request.assert_not_called()


def test_no_available_files_error(client, confluence_config):
    tool = make_tool(confluence_config, [])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "a.json"})
    assert "none" in str(exc.value)


def test_generated_files_none_message(client, confluence_config):
    tool = make_tool(confluence_config, source=False)
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "a.json"})
    assert "generated files are not available here" in str(exc.value)
    client.request.assert_not_called()


def test_empty_file_names_file(client, confluence_config):
    tool = make_tool(confluence_config, [gen("empty.json", data=b"")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "empty.json"})
    assert "empty.json" in str(exc.value) and "Successfully" not in str(exc.value)


@pytest.mark.parametrize("failure", [None, RuntimeError("boom")])
def test_upload_failure_names_file(client, confluence_config, failure):
    client.attach_content.side_effect = failure if failure else (lambda **kw: None)
    tool = make_tool(confluence_config, [gen("a.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "a.json"})
    assert "a.json" in str(exc.value) and "Successfully" not in str(exc.value)


def test_second_file_failure_lists_first_as_attached(client, confluence_config):
    def attach(**kw):
        if kw["name"] == "b.json":
            raise RuntimeError("boom")
        return {"id": "1", "title": kw["name"]}

    client.attach_content.side_effect = attach
    tool = make_tool(confluence_config, [gen("a.json"), gen("b.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"files": ["a.json", "b.json"]})
    msg = str(exc.value)
    assert "'b.json'" in msg and "Already attached: a.json" in msg


def test_failure_lists_files_not_attempted(client, confluence_config):
    def attach(**kw):
        if kw["name"] == "b.json":
            raise RuntimeError("boom")
        return {"id": "1", "title": kw["name"]}

    client.attach_content.side_effect = attach
    tool = make_tool(confluence_config, [gen("a.json"), gen("b.json"), gen("c.json"), gen("d.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"files": ["a.json", "b.json", "c.json", "d.json"]})
    msg = str(exc.value)
    assert "Already attached: a.json" in msg and "Not attempted: c.json, d.json" in msg


def test_load_failure_after_a_success_lists_attached_and_not_attempted(client, confluence_config):
    class FailingSource(FakeSource):
        def read_generated(self, info):
            if info.name == "b.json":
                raise ToolException("File 'b.json' is no longer available.")
            return super().read_generated(info)

    tool = GenericConfluenceTool(
        config=confluence_config,
        generated_files=FailingSource([gen("a.json"), gen("b.json"), gen("c.json")]),
    )
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"files": ["a.json", "b.json", "c.json"]})
    msg = str(exc.value)
    assert "no longer available" in msg
    assert "Already attached: a.json" in msg and "Not attempted: c.json" in msg


def test_last_file_failure_has_no_not_attempted_note(client, confluence_config):
    client.attach_content.side_effect = lambda **kw: None
    tool = make_tool(confluence_config, [gen("a.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "a.json"})
    assert "Not attempted" not in str(exc.value)


def test_uploaded_file_still_attaches(client, confluence_config):
    tool = make_tool(confluence_config, uploads=[upload("notes.txt", b"hi")], source=False)
    out = tool.execute("POST", ATTACH_URL, {"file": "notes.txt"})
    assert "Successfully" in out
    assert client.attach_content.call_args.kwargs["content"] == b"hi"


@pytest.mark.parametrize(
    "method,url",
    [
        ("GET", "/rest/api/content/123/child/attachment"),
        ("GET", "/rest/api/content/123/child/attachment/55"),
        ("DELETE", "/rest/api/content/123/child/attachment/55"),
        ("POST", "/rest/api/content/1"),
    ],
)
def test_other_requests_use_plain_request(client, confluence_config, method, url):
    tool = make_tool(confluence_config, [gen("a.json")])
    tool.execute(method, url, {"title": "text about attachment"})
    client.request.assert_called_once()
    client.attach_content.assert_not_called()


@pytest.mark.parametrize(
    "method,url",
    [
        ("PUT", "/rest/api/content/123/child/attachment/9/data"),
        ("PUT", "/rest/api/content/123/child/attachment/att123/data"),
    ],
)
def test_attachment_update_form_is_not_an_upload(client, confluence_config, method, url):
    tool = make_tool(confluence_config, [gen("a.json")])
    tool.execute(method, url, {"file": "a.json"})
    client.request.assert_called_once()
    client.attach_content.assert_not_called()


def test_query_string_on_upload_url_still_uploads(client, confluence_config):
    tool = make_tool(confluence_config, [gen("a.json")])
    tool.execute("POST", ATTACH_URL + "?allowDuplicated=true", {"file": "a.json"})
    client.attach_content.assert_called_once()
    client.request.assert_not_called()


def test_listing_failure_is_a_tool_exception(client, confluence_config):
    class Broken:
        def list_generated(self):
            raise RuntimeError("db down")

        def read_generated(self, info):
            raise AssertionError

    tool = GenericConfluenceTool(config=confluence_config, generated_files=Broken())
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "a.json"})
    assert "db down" in str(exc.value)


def test_repeated_name_attached_once(client, confluence_config):
    tool = make_tool(confluence_config, [gen("a.json")])
    tool.execute("POST", ATTACH_URL, {"files": ["a.json", "a.json"]})
    assert client.attach_content.call_count == 1


def test_same_name_attach_updates_existing_attachment_via_library():
    """Rule 10: the installed atlassian library finds an existing attachment by name and posts a new version to it."""
    from atlassian import Confluence

    conf = Confluence(url="https://example.atlassian.net", token="t", cloud=False)
    with (
        patch.object(Confluence, "get", return_value={"size": 1, "results": [{"id": "att77"}]}) as get,
        patch.object(Confluence, "post", return_value={"id": "att77", "title": "r.json"}) as post,
    ):
        conf.attach_content(content=b"{}", name="r.json", content_type="application/json", page_id="123")
    assert get.call_args.kwargs["params"] == {"filename": "r.json"}
    assert post.call_args.kwargs["path"] == "rest/api/content/123/child/attachment/att77/data"


def test_description_mentions_generated_files_and_exact_name():
    from codemie_tools.core.project_management.confluence.tools import ConfluenceInput

    for text in (
        GenericConfluenceTool.model_fields["description"].default,
        ConfluenceInput.model_fields["params"].description,
    ):
        assert "generated" in text.lower()
        assert "exact" in text.lower()


def test_root_level_workspace_file_attaches(client, confluence_config):
    tool = make_tool(confluence_config, [ws("test-note-2026-11-20.txt", b"note")])
    out = tool.execute("POST", ATTACH_URL, {"file": "test-note-2026-11-20.txt"})
    assert "Successfully" in out
    kw = client.attach_content.call_args.kwargs
    assert kw["name"] == "test-note-2026-11-20.txt" and kw["content"] == b"note"


@pytest.mark.parametrize("requested", ["reports/q1/out.json", "out.json"])
def test_nested_workspace_file_attaches_by_path_or_bare_name_with_basename(client, confluence_config, requested):
    tool = make_tool(confluence_config, [ws("reports/q1/out.json"), ws("x.txt")])
    tool.execute("POST", ATTACH_URL, {"file": requested})
    assert client.attach_content.call_args.kwargs["name"] == "out.json"


def test_same_basename_in_two_folders_is_ambiguous(client, confluence_config):
    tool = make_tool(confluence_config, [ws("a/out.json"), ws("b/out.json")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "out.json"})
    assert "a/out.json" in str(exc.value) and "b/out.json" in str(exc.value)
    client.attach_content.assert_not_called()
    tool.execute("POST", ATTACH_URL, {"file": "b/out.json"})
    assert client.attach_content.call_count == 1


def test_upload_also_synced_as_workspace_row_is_not_a_clash(client, confluence_config):
    row = ws("notes.txt", b"uploaded", blob=("o", "notes.txt", "text/plain"))
    tool = make_tool(confluence_config, [row], uploads=[upload("notes.txt", b"uploaded")])
    tool.execute("POST", ATTACH_URL, {"file": "notes.txt"})
    assert client.attach_content.call_args.kwargs["content"] == b"uploaded"


def test_different_blob_with_same_name_as_upload_still_clashes(client, confluence_config):
    row = ws("notes.txt", b"other", blob=("o", "other-blob", "text/plain"))
    tool = make_tool(confluence_config, [row], uploads=[upload("notes.txt")])
    with pytest.raises(ToolException):
        tool.execute("POST", ATTACH_URL, {"file": "notes.txt"})


def test_executor_duplicate_root_and_artifact_copy_attaches_without_ambiguity(client, confluence_config):
    # The code executor leaves the export at the root and under artifacts/<turn>/ with the same content.
    tool = make_tool(
        confluence_config,
        [
            ws("artifacts/t1/p2.json", checksum="c1", blob=("o", "artifacts/t1/p2.json", "application/json")),
            ws("p2.json", checksum="c1", blob=("o", "p2.json", "application/json")),
        ],
    )
    tool.execute("POST", ATTACH_URL, {"file": "p2.json"})
    assert client.attach_content.call_args.kwargs["name"] == "p2.json"


def test_subfolder_export_and_artifact_copy_attach_by_bare_name(client, confluence_config):
    tool = make_tool(
        confluence_config,
        [ws("reports/p2.json", checksum="c1"), ws("artifacts/t1/p2.json", checksum="c1")],
    )
    tool.execute("POST", ATTACH_URL, {"file": "p2.json"})
    assert client.attach_content.call_args.kwargs["name"] == "p2.json"


def test_image_style_artifact_only_file_attaches_by_name_and_by_path(client, confluence_config):
    tool = make_tool(confluence_config, [ws("artifacts/t1/pic.png", data=b"png", checksum="c9")])
    tool.execute("POST", ATTACH_URL, {"file": "pic.png"})
    tool.execute("POST", ATTACH_URL, {"file": "artifacts/t1/pic.png"})
    assert [c.kwargs["name"] for c in client.attach_content.call_args_list] == ["pic.png", "pic.png"]


def test_two_artifact_turns_with_different_content_are_ambiguous(client, confluence_config):
    tool = make_tool(
        confluence_config,
        [ws("artifacts/t1/r.json", data=b"1", checksum="a"), ws("artifacts/t2/r.json", data=b"2", checksum="b")],
    )
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"file": "r.json"})
    assert "artifacts/t1/r.json" in str(exc.value) and "artifacts/t2/r.json" in str(exc.value)
    client.attach_content.assert_not_called()


def test_two_names_resolving_to_one_file_upload_it_once(client, confluence_config):
    tool = make_tool(confluence_config, [ws("reports/q1/out.json")])
    tool.execute("POST", ATTACH_URL, {"files": ["out.json", "reports/q1/out.json"]})
    assert client.attach_content.call_count == 1


def test_distinct_files_with_same_basename_are_refused(client, confluence_config):
    tool = make_tool(confluence_config, [ws("a/out.json"), ws("b/out.json", b"other")])
    with pytest.raises(ToolException) as exc:
        tool.execute("POST", ATTACH_URL, {"files": ["a/out.json", "b/out.json"]})
    assert "one at a time" in str(exc.value)
    client.attach_content.assert_not_called()
