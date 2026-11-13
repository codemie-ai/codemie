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

"""The browser scripts live in .js files next to navigation.py and are inlined exactly as written."""

import base64
import hashlib
from importlib.resources import files

import pytest

from codemie.hosted_apps_gateway import navigation
from codemie.hosted_apps_gateway.navigation import navigation_suffix

SCRIPTS = ("content_hook.js", "content_broker.js", "viewer_wrapper.js")
VIEWER = "http://w-1.apps.localhost:8081"
UI = ("http://localhost:5173", "https://codemie.example.com")


def packaged(name):
    """The file as it ships, without its leading license header block, which is never served."""
    text = files("codemie.hosted_apps_gateway").joinpath(name).read_bytes().decode("utf-8")
    assert text.startswith("// Copyright ")
    return text.split("\n\n", 1)[1]


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_script_ships_inside_the_package(name):
    text = packaged(name)
    assert text.strip() and "</script" not in text.lower() and "<!--" not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_the_license_header_is_kept_in_the_file_but_not_served(name):
    served = navigation.inline_script(name)
    assert served == packaged(name)
    assert "Copyright" not in served and "Apache License" not in served


@pytest.mark.parametrize("embedded", [True, False])
def test_the_served_suffix_is_the_files_plus_the_request_configuration(embedded):
    config = 'const viewer = "' + VIEWER + '", ui = ["' + '", "'.join(UI) + '"], embedded = ' + str(embedded).lower()
    body = packaged("content_hook.js") + (packaged("content_broker.js") if embedded else "")
    expected = "\n<script>(() => {\n" + config + ";\n" + body + "})();</script>"
    assert navigation_suffix(VIEWER, UI, embedded=embedded) == expected.encode()


def test_the_wrapper_hash_is_taken_from_the_file_bytes():
    text = packaged("viewer_wrapper.js")
    assert text == navigation.WRAPPER_SCRIPT
    assert base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() == navigation.WRAPPER_SCRIPT_HASH


@pytest.mark.parametrize("markup", ["</script>", "</SCRIPT >", "<script>", "<!--"])
def test_a_script_that_could_break_out_of_its_element_is_refused(tmp_path, monkeypatch, markup):
    script = tmp_path / "evil.js"
    script.write_text("const x = '" + markup + "';\n")
    monkeypatch.setattr(navigation, "__file__", str(tmp_path / "navigation.py"))
    with pytest.raises(RuntimeError):
        navigation.inline_script("evil.js")
