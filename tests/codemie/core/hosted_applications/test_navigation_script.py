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
// One BroadcastChannel bus for every window of the content origin, delivering to the other objects.
const channels = [];
class FakeBroadcastChannel {
  constructor(name) { this.name = name; this.listeners = []; channels.push(this); }
  addEventListener(type, listener) { if (type === 'message') this.listeners.push(listener); }
  postMessage(data) {
    const copy = structuredClone(data);
    for (const other of channels) {
      if (other !== this && other.name === this.name) {
        for (const listener of other.listeners) listener({data: structuredClone(copy)});
      }
    }
  }
}
let uuids = 0;
class Element {
  constructor(attributes, href) { this.attributes = attributes; this.href = href; }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
  closest(selector) { return selector === 'a[href]' && 'href' in this.attributes ? this : null; }
}
const makeWindow = options => {
  const posted = [], opened = [], listeners = {}, warned = [];
  const page = new URL(options.href);
  const location = {href: page.href, origin: page.origin, pathname: page.pathname, search: page.search, hash: page.hash};
  if (options.ancestors) location.ancestorOrigins = Object.freeze([...options.ancestors]);
  const session = new Map(Object.entries(options.session || {}));
  const context = {
    URL, Reflect, Element, location, JSON, MessageEvent, structuredClone,
    BroadcastChannel: FakeBroadcastChannel,
    crypto: {randomUUID: () => 'channel-' + String(++uuids).padStart(4, '0')},
    console: {warn: (...args) => warned.push(args.map(String).join(' '))},
    history: {pushState() {}, replaceState() {}},
    addEventListener(type, listener, capture) { (listeners[type] = listeners[type] || []).push({listener, capture}); },
    dispatchEvent(event) {
      for (const {listener} of listeners[event.type] || []) listener(event);
      return true;
    },
    // Elements of the page by name attribute, as {name, tag} in options.named.
    document: {getElementsByName(name) {
      return (options.named || []).filter(entry => entry.name === name).map(entry => ({tagName: entry.tag}));
    }},
    sessionStorage: {
      getItem: key => session.has(key) ? session.get(key) : null,
      setItem(key, value) { session.set(key, String(value)); },
      removeItem(key) { session.delete(key); },
    },
  };
  context.window = context;
  // The content frame's name, which the CodeMie page sets.
  context.name = options.name || '';
  context.opener = options.nativeOpener ? {native: 'opener'} : null;
  context.parent = options.top ? context : {postMessage(message, origin) { posted.push({message, origin}); }};
  context.open = function(...args) { opened.push(args); return {native: true}; };
  context.close = function() { context.nativeClosed = (context.nativeClosed || 0) + 1; };
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
  // A page event such as pageshow or pagehide.
  const emit = (type, init = {}) => context.dispatchEvent({type, ...init});
  Object.assign(context, {link, fire, emit, posted, warned});
  return {context, posted, opened, listeners, warned};
};
const main = makeWindow(config);
// Another window of the same version, for example the one CodeMie opened for a stand-in.
const spawn = (overrides = {}) => makeWindow({...config, ancestors: config.ancestors, ...overrides}).context;
Promise.resolve(vm.runInContext('(' + steps + ')', Object.assign(main.context, {spawn}))()).then(result => {
  process.stdout.write(JSON.stringify({posted: main.posted, opened: main.opened, warned: main.warned, result,
    capture: Object.fromEntries(Object.entries(main.listeners).map(([type, entries]) =>
      [type, entries.map(entry => entry.capture === true)]))}));
});
"""


def run(
    steps="() => null",
    *,
    embedded=True,
    ancestors=(UI,),
    ui=(UI,),
    href=CONTENT + "/index.html",
    top=False,
    named=(),
    session=None,
    native_opener=False,
    name="",
):
    suffix = navigation_suffix(VIEWER, ui, embedded=embedded).decode()
    assert suffix.startswith("\n<script>") and suffix.endswith("</script>")
    assert suffix.count("<script>") == 1 and suffix.count("</script>") == 1
    config = {
        "href": href,
        "ancestors": list(ancestors) if ancestors is not None else None,
        "top": top,
        "named": [{"name": name, "tag": tag} for name, tag in named],
        "session": session or {},
        "nativeOpener": native_opener,
        "name": name,
    }
    payload = json.dumps({"script": suffix[len("\n<script>") : -len("</script>")], "config": config, "steps": steps})
    completed = subprocess.run(
        [NODE, "-e", HARNESS], input=payload, capture_output=True, text=True, timeout=30, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def opening(target, name="", channel=None, features=None):
    return {"type": "codemie-hosted-open", "target": target, "name": name, "channel": channel, "features": features}


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
    steps = "() => { const w = " + call + "; w.focus(); w.blur(); w.postMessage('x', '*');"
    steps += " return {closed: w.closed, keys: Object.keys(w).sort()}; }"
    outcome = run(steps)
    features = {"popup": True} if "popup" in call else None
    # The stub's focus() asks again for a named window only, on the same channel.
    assert messages(outcome, "codemie-hosted-open") == [
        {"message": opening(target, name, "channel-0001", features), "origin": UI}
    ] * (2 if name else 1)
    assert outcome["opened"] == []
    assert outcome["result"] == {"closed": False, "keys": ["blur", "close", "closed", "focus", "postMessage"]}


@pytest.mark.parametrize("features", ["noopener", "popup,noreferrer", "noopener=yes", "width=400, noopener=1"])
def test_noopener_returns_null_like_the_platform(features):
    outcome = run("() => window.open('/p', 'presenter', '" + features + "')")
    assert outcome["result"] is None
    # No stand-in, so no channel and no relay.
    assert [
        (entry["message"]["target"], entry["message"]["channel"]) for entry in messages(outcome, "codemie-hosted-open")
    ] == [("/p", None)]


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
    assert run()["capture"] == {
        "popstate": [False],
        "hashchange": [False],
        "pageshow": [False],
        "pagehide": [False],
        "click": [False],
        "auxclick": [False],
    }


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
    assert messages(outcome, "codemie-hosted-open") == [{"message": opening("/preview", name), "origin": UI}]


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
    assert messages(outcome, "codemie-hosted-open") == [{"message": opening("/detail/7"), "origin": UI}]


def test_focus_on_a_named_stub_asks_again_so_a_closed_window_reopens():
    outcome = run("() => { const w = window.open('/deck.html?presenter=1', 'presenter'); w.focus(); w.focus(); }")
    assert [entry["message"] for entry in messages(outcome, "codemie-hosted-open")] == [
        opening("/deck.html?presenter=1", "presenter", "channel-0001")
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
    assert messages(outcome, "codemie-hosted-open") == [{"message": opening("/detail/7"), "origin": UI}]


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


# --- Window features, relay channels and the opener (EPMCDME-17157) ---

OPENER_KEY = "codemie-hosted-opener"
# The CodeMie page names the content frame codemie-hosted-<32 hex>, plus -opener-<channel> for a window it opened
# for a stand-in.
FRAME = "codemie-hosted-" + "0123456789abcdef" * 2


def opened_frame(channel):
    return FRAME + "-opener-" + channel


@pytest.mark.parametrize(
    ("features", "expected"),
    [
        ("width=1180,height=720", {"popup": True, "width": 1180, "height": 720}),
        ("WIDTH=1180, Height = 720", {"popup": True, "width": 1180, "height": 720}),
        ("popup", {"popup": True}),
        ("popup=1 left=20 top=30", {"popup": True, "left": 20, "top": 30}),
        ("popup=0,width=500", {"width": 500}),
        (
            "width=50,height=20000,left=-5,top=99999",
            {"popup": True, "width": 100, "height": 10000, "left": 0, "top": 10000},
        ),
        ("width=abc,height=12.7,left=1e3,top=", {"popup": True}),
        (
            "innerWidth=640,innerHeight=480,screenX=10,screenY=20",
            {"popup": True, "width": 640, "height": 480, "left": 10, "top": 20},
        ),
        ("location=yes,toolbar=yes,menubar=yes,resizable=yes,scrollbars=yes,status=yes", None),
        ("", None),
    ],
)
def test_window_features_are_parsed_like_the_browser_and_clamped(features, expected):
    outcome = run("() => { window.open('/p', '', " + json.dumps(features) + "); }")
    assert [entry["message"]["features"] for entry in messages(outcome, "codemie-hosted-open")] == [expected]


def test_window_open_without_features_sends_null_features():
    outcome = run("() => { window.open('/p'); }")
    assert messages(outcome, "codemie-hosted-open")[0]["message"] == opening("/p", "", "channel-0001")


def test_each_stand_in_has_its_own_channel_and_a_reused_name_keeps_its_window():
    outcome = run(
        "() => { const a = window.open('/a'), b = window.open('/b', '_blank');"
        " const p = window.open('/p1', 'presenter'), q = window.open('/p2', 'presenter');"
        " return {distinct: a !== b, same: p === q}; }"
    )
    assert [entry["message"]["channel"] for entry in messages(outcome, "codemie-hosted-open")] == [
        "channel-0001",
        "channel-0002",
        "channel-0003",
        "channel-0003",
    ]
    assert outcome["result"] == {"distinct": True, "same": True}


RELAY = (
    "() => {"
    " const got = [];"
    " addEventListener('message', e => got.push({to: 'main', data: e.data, origin: e.origin, source: e.source === w}));"
    " const w = window.open('/notes.html', 'codemie-presenter', 'width=1180,height=720');"
    " const child = spawn({href: '" + CONTENT + "/notes.html', name: '" + opened_frame('channel-0001') + "'});"
    " child.addEventListener('message', e => got.push("
    "   {to: 'child', data: e.data, origin: e.origin, source: e.source === child.opener}));"
    " %s"
    " return {got, child: child.posted.map(entry => entry.message.type)};"
    "}"
)


def test_messages_relay_both_ways_with_the_stand_in_as_their_source():
    outcome = run(
        RELAY
        % (
            "child.opener.postMessage({type: 'cm-ready'}, '*');"
            " w.postMessage({type: 'cm-sync', index: 3}, '*');"
            " child.opener.postMessage({type: 'cm-nav', delta: 1}, location.origin);"
        )
    )
    assert outcome["result"]["got"] == [
        {"to": "main", "data": {"type": "cm-ready"}, "origin": CONTENT, "source": True},
        {"to": "child", "data": {"type": "cm-sync", "index": 3}, "origin": CONTENT, "source": True},
        {"to": "main", "data": {"type": "cm-nav", "delta": 1}, "origin": CONTENT, "source": True},
    ]
    # CodeMie never sees the relayed messages.
    assert {entry["message"]["type"] for entry in outcome["posted"]} == {
        "codemie-hosted-location",
        "codemie-hosted-open",
    }
    assert set(outcome["result"]["child"]) == {"codemie-hosted-location"}


def test_a_target_origin_other_than_this_one_drops_the_message_like_the_browser():
    outcome = run(
        RELAY
        % (
            "w.postMessage('evil', 'https://evil.example');"
            " w.postMessage('bad', 'not a url');"
            " w.postMessage('slash', '/');"
            " w.postMessage('own', location.origin);"
            " w.postMessage('options', {targetOrigin: '*'});"
            " w.postMessage('options-evil', {targetOrigin: 'https://evil.example'});"
            " w.postMessage('default');"
        )
    )
    assert [entry["data"] for entry in outcome["result"]["got"]] == ["slash", "own", "options", "default"]


def test_a_transfer_list_is_dropped_with_one_warning():
    outcome = run(
        RELAY
        % (
            "const buffer = new ArrayBuffer(8);"
            " w.postMessage({n: 1}, '*', [buffer]);"
            " w.postMessage({n: 2}, {targetOrigin: '*', transfer: [buffer]});"
        )
    )
    assert [entry["data"] for entry in outcome["result"]["got"]] == [{"n": 1}, {"n": 2}]
    assert len(outcome["warned"]) == 1 and "transfer" in outcome["warned"][0]


def test_messages_on_another_channel_or_from_the_same_side_are_ignored():
    outcome = run(
        RELAY
        % (
            "const stranger = spawn({name: '" + opened_frame('channel-9999') + "'});"
            " stranger.opener.postMessage('not mine', '*');"
            " const twin = spawn({name: '" + opened_frame('channel-0001') + "'});"
            " twin.opener.postMessage('from the presenter twin', '*');"
        )
    )
    # Only the child side of channel-0001 reaches the opener; nothing reaches another child.
    assert [(entry["to"], entry["data"]) for entry in outcome["result"]["got"]] == [("main", "from the presenter twin")]


def test_closed_follows_the_opened_window_and_close_asks_codemie_to_close_it():
    outcome = run(
        "() => {"
        " const w = window.open('/notes.html', 'codemie-presenter');"
        " const states = [w.closed];"
        " const child = spawn({name: '" + opened_frame('channel-0001') + "'});"
        " child.emit('pageshow'); states.push(w.closed);"
        " child.emit('pagehide'); states.push(w.closed);"
        " child.emit('pageshow'); states.push(w.closed);"
        " states.push(child.opener.closed); emit('pagehide'); states.push(child.opener.closed);"
        " emit('pageshow', {persisted: true}); states.push(child.opener.closed);"
        " w.close(); states.push(w.closed);"
        " return states;"
        "}"
    )
    assert outcome["result"] == [False, False, True, False, False, True, False, True]
    assert messages(outcome, "codemie-hosted-close") == [
        {"message": {"type": "codemie-hosted-close", "channel": "channel-0001"}, "origin": UI}
    ]


@pytest.mark.parametrize(
    ("name", "native_opener", "embedded", "expected"),
    [
        (opened_frame("channel-0001"), False, True, "stand-in"),
        (opened_frame("channel-0001"), True, True, "native"),
        (FRAME, False, True, "none"),
        ("", False, True, "none"),
        (opened_frame("bad_id"), False, True, "none"),
        (opened_frame("short"), False, True, "none"),
        (opened_frame("channel-0001"), False, False, "none"),
    ],
)
def test_the_opener_stand_in_appears_only_for_a_launch_with_an_opener_and_no_native_one(
    name, native_opener, embedded, expected
):
    outcome = run(
        "() => window.opener === null ? 'none' : window.opener.native ? 'native' : "
        "(typeof window.opener.postMessage === 'function' ? 'stand-in' : 'other')",
        name=name,
        native_opener=native_opener,
        embedded=embedded,
        ancestors=(UI,) if embedded else (VIEWER,),
    )
    assert outcome["result"] == expected


def test_the_child_tells_its_opener_it_closed_before_it_loaded_again():
    outcome = run(
        "() => { const w = window.open('/n', 'p'); const child = spawn({name: '"
        + opened_frame("channel-0001")
        + "'}); child.emit('pagehide'); const closed = w.closed;"
        " const again = spawn({name: '" + opened_frame('channel-0001') + "'}); again.emit('pageshow');"
        " return [closed, w.closed]; }"
    )
    assert outcome["result"] == [True, False]


@pytest.mark.parametrize(
    ("name", "stored", "kept", "opener"),
    [
        # The CodeMie page names the frame: its name decides.
        (opened_frame("channel-0002"), "channel-0001", "channel-0002", True),
        (opened_frame("channel-0002"), None, "channel-0002", True),
        (FRAME, "channel-0001", None, False),
        (FRAME.upper(), "channel-0001", "channel-0001", True),
        # The app renamed its window, or the name is not CodeMie's: what an earlier page stored stays.
        ("deck-main", "channel-0001", "channel-0001", True),
        ("", "channel-0001", "channel-0001", True),
        ("codemie-hosted-0123", "channel-0001", "channel-0001", True),
        (opened_frame("bad_id"), "channel-0001", "channel-0001", True),
        ("deck-main", None, None, False),
    ],
)
def test_the_frame_name_records_or_clears_the_opener_channel_for_this_tab(name, stored, kept, opener):
    session = {OPENER_KEY: stored} if stored else {}
    outcome = run(
        "() => ({kept: sessionStorage.getItem('" + OPENER_KEY + "'), opener: window.opener !== null})",
        name=name,
        session=session,
    )
    assert outcome["result"] == {"kept": kept, "opener": opener}


def test_a_launch_outside_codemie_leaves_the_stored_channel_alone():
    outcome = run(
        "() => sessionStorage.getItem('" + OPENER_KEY + "')",
        name=FRAME,
        session={OPENER_KEY: "channel-0001"},
        embedded=False,
        ancestors=(VIEWER,),
    )
    assert outcome["result"] == "channel-0001"


@pytest.mark.parametrize(("ancestors", "origins"), [((UI,), [UI]), (None, [UI, OTHER_UI]), (("null",), [UI, OTHER_UI])])
def test_window_close_asks_codemie_to_close_this_window(ancestors, origins):
    outcome = run(
        "() => { const value = window.close(); return {value: value === undefined, native: window.nativeClosed || 0}; }",
        ancestors=ancestors,
        ui=(UI, OTHER_UI),
    )
    assert messages(outcome, "codemie-hosted-close-self") == [
        {"message": {"type": "codemie-hosted-close-self"}, "origin": origin} for origin in origins
    ]
    assert outcome["result"] == {"value": True, "native": 0}


def test_an_opened_window_that_closes_itself_tells_its_opener_at_once():
    outcome = run(
        "() => {"
        " const w = window.open('/picker.html', 'picker');"
        " const child = spawn({name: '" + opened_frame("channel-0001") + "'});"
        " child.emit('pageshow');"
        " child.opener.postMessage({picked: 7}, '*');"
        " const before = w.closed;"
        " child.close();"
        " return {before, after: w.closed, child: child.posted.map(entry => entry.message), native: child.nativeClosed || 0};"
        "}"
    )
    assert outcome["result"]["before"] is False and outcome["result"]["after"] is True
    assert {"type": "codemie-hosted-close-self"} in outcome["result"]["child"]
    assert outcome["result"]["native"] == 0
    # The opener page itself is not asked to close.
    assert messages(outcome, "codemie-hosted-close-self") == []


def test_a_window_not_embedded_by_codemie_keeps_the_native_close():
    outcome = run("() => { window.close(); return window.nativeClosed || 0; }", embedded=False, ancestors=(VIEWER,))
    assert outcome["result"] == 1
    assert messages(outcome, "codemie-hosted-close-self") == []
