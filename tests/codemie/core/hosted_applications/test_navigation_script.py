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

"""The script appended to hosted HTML navigations, run in Node against a fake frame.

It must parse, report the location only to the frame's real parent, and, in the embedded viewer only,
ask CodeMie to open same-origin windows and new-tab links instead of opening them in the sandbox.
"""

import json
import shutil
import subprocess

import pytest

from codemie.hosted_apps_gateway.navigation import navigation_suffix

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js runs the injected browser script")

CONTENT = "http://v-1.apps.localhost:8081"
VIEWER = "http://w-1.apps.localhost:8081"
UI = "http://localhost:5173"
OTHER_UI = "https://codemie.example.com"

HARNESS = r"""
const vm = require('vm');
const {script, config, steps} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const posted = [], opened = [], listeners = {};
const page = new URL(config.href);
const location = {href: page.href, origin: page.origin, pathname: page.pathname, search: page.search, hash: page.hash};
if (config.ancestors) location.ancestorOrigins = Object.freeze([...config.ancestors]);
class Element {
  constructor(attributes, href) { this.attributes = attributes; this.href = href; }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
  closest(selector) { return selector === 'a[href]' && 'href' in this.attributes ? this : null; }
}
const context = {
  URL, Reflect, Element, location, JSON,
  history: {pushState() {}, replaceState() {}},
  addEventListener(type, listener, capture) { (listeners[type] = listeners[type] || []).push({listener, capture}); },
  // Elements of the page by name attribute, as {name, tag} in config.named.
  document: {getElementsByName(name) {
    return (config.named || []).filter(entry => entry.name === name).map(entry => ({tagName: entry.tag}));
  }},
};
context.window = context;
context.parent = config.top ? context : {postMessage(message, origin) { posted.push({message, origin}); }};
context.open = function(...args) { opened.push(args); return {native: true}; };
vm.createContext(context);
vm.runInContext(script, context);
const link = (attributes) => new Element(attributes, new URL(attributes.href, page).href);
// Dispatch as a browser does for a listener on window: capture listeners, then the page's own
// handler on the target (`app`), then window's bubble listeners.
const fire = (type, target, init = {}, app = null) => {
  const event = {type, target, button: 0, ctrlKey: false, metaKey: false, shiftKey: false, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; }, ...init};
  for (const {listener, capture} of listeners[type] || []) if (capture) listener(event);
  if (app) app(event);
  for (const {listener, capture} of listeners[type] || []) if (!capture) listener(event);
  return event.defaultPrevented;
};
const result = vm.runInContext('(' + steps + ')', Object.assign(context, {link, fire}))();
process.stdout.write(JSON.stringify({posted, opened, result, capture: Object.fromEntries(
  Object.entries(listeners).map(([type, entries]) => [type, entries.map(entry => entry.capture === true)]))}));
"""


def run(
    steps="() => null", *, embedded=True, ancestors=(UI,), ui=(UI,), href=CONTENT + "/index.html", top=False, named=()
):
    suffix = navigation_suffix(VIEWER, ui, embedded=embedded).decode()
    assert suffix.startswith("\n<script>") and suffix.endswith("</script>")
    assert suffix.count("<script>") == 1 and suffix.count("</script>") == 1
    config = {
        "href": href,
        "ancestors": list(ancestors) if ancestors is not None else None,
        "top": top,
        "named": [{"name": name, "tag": tag} for name, tag in named],
    }
    payload = json.dumps({"script": suffix[len("\n<script>") : -len("</script>")], "config": config, "steps": steps})
    completed = subprocess.run(
        [NODE, "-e", HARNESS], input=payload, capture_output=True, text=True, timeout=30, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def messages(outcome, kind):
    return [entry for entry in outcome["posted"] if entry["message"]["type"] == kind]


def test_the_location_report_goes_to_the_embedding_codemie_frame_not_the_viewer():
    outcome = run(href=CONTENT + "/deck.html?x=1#/2")
    assert messages(outcome, "codemie-hosted-location") == [
        {"message": {"type": "codemie-hosted-location", "target": "/deck.html?x=1#/2"}, "origin": UI}
    ]


def test_the_standalone_viewer_still_receives_the_location_and_nothing_is_brokered():
    outcome = run(
        "() => window.open('/deck.html?presenter=1', 'presenter')",
        embedded=False,
        ancestors=(VIEWER,),
    )
    assert outcome["posted"] == [
        {"message": {"type": "codemie-hosted-location", "target": "/index.html"}, "origin": VIEWER}
    ]
    assert outcome["opened"] == [["/deck.html?presenter=1", "presenter"]]
    assert outcome["result"] == {"native": True}
    assert "click" not in outcome["capture"]


def test_without_ancestor_origins_messages_go_to_each_codemie_origin_only():
    outcome = run("() => window.open('/p', 'presenter')", ancestors=None, ui=(UI, OTHER_UI))
    assert {entry["origin"] for entry in outcome["posted"]} == {UI, OTHER_UI}
    assert [entry["origin"] for entry in messages(outcome, "codemie-hosted-open")] == sorted((UI, OTHER_UI))
    assert outcome["opened"] == []


@pytest.mark.parametrize("ancestors", [("null",), ("null", "null")])
def test_opaque_ancestor_origins_fall_back_like_missing_ones(ancestors):
    # Firefox reports "null" ancestors for the content frame (seen with Firefox 153).
    outcome = run("() => window.open('/p', 'presenter')", ancestors=ancestors, ui=(UI, OTHER_UI))
    assert [entry["origin"] for entry in messages(outcome, "codemie-hosted-location")] == [UI, OTHER_UI]
    assert [entry["origin"] for entry in messages(outcome, "codemie-hosted-open")] == [UI, OTHER_UI]
    assert outcome["opened"] == []
    standalone = run("() => window.open('/p', 'presenter')", embedded=False, ancestors=ancestors)
    assert standalone["posted"] == [
        {"message": {"type": "codemie-hosted-location", "target": "/index.html"}, "origin": VIEWER}
    ]
    assert standalone["opened"] == [["/p", "presenter"]]


@pytest.mark.parametrize("ancestor", [VIEWER, "https://attacker.example"])
def test_a_frame_not_embedded_by_codemie_keeps_the_native_window_open(ancestor):
    outcome = run("() => window.open('/p', 'presenter')", ancestors=(ancestor,))
    assert messages(outcome, "codemie-hosted-open") == []
    assert outcome["opened"] == [["/p", "presenter"]]
    assert all(entry["origin"] == VIEWER for entry in outcome["posted"])
    if ancestor != VIEWER:
        assert outcome["posted"] == []


def test_a_top_level_document_posts_nothing():
    outcome = run("() => window.open('/p')", ancestors=(), top=True)
    assert outcome["posted"] == []
    assert outcome["opened"] == [["/p"]]


@pytest.mark.parametrize(
    ("call", "target", "name"),
    [
        ("window.open('/deck.html?presenter=1#/3', 'presenter')", "/deck.html?presenter=1#/3", "presenter"),
        ("window.open('?presenter=1')", "/index.html?presenter=1", ""),
        ("window.open(new URL('/print', location.href), '')", "/print", ""),
        ("window.open('" + CONTENT + "/detail/7', '_blank', 'popup')", "/detail/7", ""),
        ("window.open('/detail/7', '_BLANK')", "/detail/7", ""),
        ("window.open('#notes', 3)", "/index.html#notes", ""),
    ],
)
def test_same_origin_window_open_asks_codemie_and_returns_a_stub(call, target, name):
    steps = "() => { const w = " + call + "; w.close(); w.focus(); w.blur(); w.postMessage('x', '*');"
    steps += " return {closed: w.closed, keys: Object.keys(w).sort()}; }"
    outcome = run(steps)
    # The stub's focus() asks again for a named window only.
    assert messages(outcome, "codemie-hosted-open") == [
        {"message": {"type": "codemie-hosted-open", "target": target, "name": name}, "origin": UI}
    ] * (2 if name else 1)
    assert outcome["opened"] == []
    assert outcome["result"] == {"closed": False, "keys": ["blur", "close", "closed", "focus", "postMessage"]}


@pytest.mark.parametrize("features", ["noopener", "popup,noreferrer", "noopener=yes", "width=400, noopener=1"])
def test_noopener_returns_null_like_the_platform(features):
    outcome = run("() => window.open('/p', 'presenter', '" + features + "')")
    assert outcome["result"] is None
    assert [entry["message"]["target"] for entry in messages(outcome, "codemie-hosted-open")] == ["/p"]


def test_noopener_off_still_returns_the_stub():
    outcome = run("() => window.open('/p', 'presenter', 'noopener=0') !== null")
    assert outcome["result"] is True


@pytest.mark.parametrize(
    "call",
    [
        "window.open()",
        "window.open('')",
        "window.open(undefined, 'x')",
        "window.open('about:blank', 'x')",
        "window.open('https://example.com/', '_blank')",
        "window.open('http://v-2.apps.localhost:8081/p')",
        "window.open('javascript:void 0')",
        "window.open('/p', '_self')",
        "window.open('/p', '_top')",
        "window.open('/p', '_parent')",
    ],
)
def test_other_window_open_calls_reach_the_native_function_unchanged(call):
    outcome = run("() => { const w = " + call + "; return w; }")
    assert messages(outcome, "codemie-hosted-open") == []
    assert len(outcome["opened"]) == 1
    assert outcome["result"] == {"native": True}


def test_link_listeners_run_in_the_bubble_phase():
    assert run()["capture"] == {"popstate": [False], "hashchange": [False], "click": [False], "auxclick": [False]}


def test_a_click_the_page_cancels_itself_is_not_brokered():
    link = json.dumps({"href": "/detail/7", "target": "_blank"})
    cancelled = run("() => fire('click', link(" + link + "), {}, event => event.preventDefault())")
    assert messages(cancelled, "codemie-hosted-open") == []
    handled = run("() => fire('click', link(" + link + "), {}, event => {})")
    assert handled["result"] is True
    assert [entry["message"]["target"] for entry in messages(handled, "codemie-hosted-open")] == ["/detail/7"]


@pytest.mark.parametrize(("target", "name"), [("preview", "preview"), ("Panel-2", "Panel-2")])
def test_a_named_link_target_is_brokered_with_its_name(target, name):
    outcome = run("() => fire('click', link(" + json.dumps({"href": "/preview", "target": target}) + "))")
    assert outcome["result"] is True
    assert messages(outcome, "codemie-hosted-open") == [
        {"message": {"type": "codemie-hosted-open", "target": "/preview", "name": name}, "origin": UI}
    ]


@pytest.mark.parametrize("tag", ["IFRAME", "FRAME", "iframe"])
def test_a_link_to_a_frame_inside_the_page_stays_in_that_frame(tag):
    outcome = run(
        "() => fire('click', link(" + json.dumps({"href": "/page/2", "target": "content"}) + "))",
        named=(("content", tag),),
    )
    assert outcome["result"] is False
    assert messages(outcome, "codemie-hosted-open") == []


def test_window_open_into_a_frame_inside_the_page_stays_native():
    outcome = run("() => window.open('/page/2', 'content')", named=(("content", "IFRAME"),))
    assert messages(outcome, "codemie-hosted-open") == []
    assert outcome["opened"] == [["/page/2", "content"]]
    assert outcome["result"] == {"native": True}


def test_a_name_shared_only_with_other_elements_is_still_a_window():
    named = (("preview", "INPUT"), ("preview", "META"))
    link = run("() => fire('click', link(" + json.dumps({"href": "/p", "target": "preview"}) + "))", named=named)
    opened = run("() => window.open('/p', 'preview') !== null", named=named)
    for outcome in (link, opened):
        assert outcome["result"] is True
        assert [entry["message"]["name"] for entry in messages(outcome, "codemie-hosted-open")][:1] == ["preview"]


def test_a_modified_click_on_a_frame_link_still_opens_a_new_window():
    outcome = run(
        "() => fire('click', link(" + json.dumps({"href": "/p", "target": "content"}) + "), {metaKey: true})",
        named=(("content", "IFRAME"),),
    )
    assert outcome["result"] is True
    assert [entry["message"]["name"] for entry in messages(outcome, "codemie-hosted-open")] == [""]


@pytest.mark.parametrize("target", ["_new", "_NEW", "_window", "_"])
def test_other_underscore_link_targets_open_a_new_window_like_blank(target):
    outcome = run("() => fire('click', link(" + json.dumps({"href": "/detail/7", "target": target}) + "))")
    assert outcome["result"] is True
    assert messages(outcome, "codemie-hosted-open") == [
        {"message": {"type": "codemie-hosted-open", "target": "/detail/7", "name": ""}, "origin": UI}
    ]


def test_focus_on_a_named_stub_asks_again_so_a_closed_window_reopens():
    outcome = run("() => { const w = window.open('/deck.html?presenter=1', 'presenter'); w.focus(); w.focus(); }")
    assert [entry["message"] for entry in messages(outcome, "codemie-hosted-open")] == [
        {"type": "codemie-hosted-open", "target": "/deck.html?presenter=1", "name": "presenter"}
    ] * 3


@pytest.mark.parametrize("name", ["", "_blank"])
def test_focus_on_an_unnamed_stub_opens_nothing_more(name):
    outcome = run("() => window.open('/p', " + json.dumps(name) + ").focus()")
    assert len(messages(outcome, "codemie-hosted-open")) == 1


@pytest.mark.parametrize(
    ("attributes", "event", "init"),
    [
        ({"href": "/detail/7", "target": "_blank"}, "click", {}),
        ({"href": "/detail/7", "target": "_BLANK"}, "click", {}),
        ({"href": "/detail/7"}, "click", {"metaKey": True}),
        ({"href": "/detail/7"}, "click", {"ctrlKey": True}),
        ({"href": "/detail/7"}, "click", {"shiftKey": True}),
        ({"href": "/detail/7"}, "auxclick", {"button": 1}),
        ({"href": "/detail/7", "target": "panel"}, "click", {"metaKey": True}),
        ({"href": "/detail/7", "target": "_self"}, "click", {"ctrlKey": True}),
        ({"href": "/detail/7", "target": "_top"}, "auxclick", {"button": 1}),
    ],
)
def test_new_tab_links_to_the_same_origin_ask_codemie(attributes, event, init):
    outcome = run(
        "() => fire(" + json.dumps(event) + ", link(" + json.dumps(attributes) + "), " + json.dumps(init) + ")"
    )
    assert outcome["result"] is True
    assert messages(outcome, "codemie-hosted-open") == [
        {"message": {"type": "codemie-hosted-open", "target": "/detail/7", "name": ""}, "origin": UI}
    ]


@pytest.mark.parametrize(
    ("attributes", "event", "init"),
    [
        ({"href": "/detail/7"}, "click", {}),
        ({"href": "/detail/7", "target": "_self"}, "click", {}),
        ({"href": "/detail/7", "target": "_top"}, "click", {}),
        ({"href": "/detail/7", "target": "_PARENT"}, "click", {}),
        ({"href": "https://example.com/", "target": "panel"}, "click", {}),
        ({"href": "https://example.com/", "target": "_blank"}, "click", {}),
        ({"href": "/detail/7", "target": "_blank"}, "click", {"button": 2}),
        ({"href": "/detail/7"}, "auxclick", {"button": 2}),
        ({"href": "/report.csv", "target": "_blank", "download": ""}, "click", {}),
        ({"target": "_blank"}, "click", {}),
        ({"href": "/detail/7", "target": "_blank"}, "click", {"defaultPrevented": True}),
    ],
)
def test_other_link_clicks_are_left_to_the_browser(attributes, event, init):
    outcome = run(
        "() => { const prevented = "
        + json.dumps(init.get("defaultPrevented", False))
        + "; return fire("
        + json.dumps(event)
        + ", link("
        + json.dumps(attributes)
        + "), "
        + json.dumps(init)
        + ") && !prevented; }"
    )
    assert outcome["result"] is False
    assert messages(outcome, "codemie-hosted-open") == []


def test_a_click_outside_any_element_is_ignored():
    outcome = run("() => fire('click', {closest() { throw new Error('not an element'); }}, {metaKey: true})")
    assert outcome["result"] is False
    assert messages(outcome, "codemie-hosted-open") == []


def test_origins_cannot_break_out_of_the_script():
    hostile = 'http://x.example"</script><script>alert(1)//'
    suffix = navigation_suffix(hostile, (hostile,), embedded=True).decode()
    assert suffix.count("</script>") == 1 and suffix.count("<script>") == 1
