// Copyright 2026 EPAM Systems, Inc. (“EPAM”)
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Window broker for an embedded launch. Appended after content_hook.js (it uses `ui`, `parents`
// and `post` from there) only when a CodeMie page embeds the content.
//
// A window of the content's own pages (presenter view, print view, a detail tab) cannot work from
// the sandbox: the pop-up inherits it and has no viewing session. The CodeMie page opens it
// instead, as an ordinary launch of the same version at that page. Only same-origin pages are
// brokered; about:blank, other origins, _self/_parent/_top and frames of the page keep the browser's behaviour.
// Forms and a blank window navigated later are not brokered.
//
// The two windows talk through a relay: a BroadcastChannel of the content origin, which both
// windows share because they run the same version. window.open returns a stand-in whose
// postMessage goes to the opened page, and that page gets a stand-in window.opener. CodeMie does
// not see these messages.
if (parents.length && parents.every(origin => ui.includes(origin))) {
  const local = value => {
    try {
      const url = new URL(value, location.href);
      return url.origin === location.origin ? url.pathname + url.search + url.hash : null;
    } catch {
      // A value that is not a URL is not brokered.
      return null;
    }
  };

  // A reserved target (_blank, _self, _parent, _top) or no name means a new window each time.
  const windowName = name => typeof name === 'string' && name && !name.startsWith('_') ? name : '';
  // _self, _parent and _top navigate a frame that already exists; every other "_" name is a new window.
  const frameLocal = name => typeof name === 'string' && ['_self', '_parent', '_top'].includes(name.toLowerCase());
  // A name an iframe or frame of this page carries targets that frame, not a window.
  const inPage = name => Array.prototype.some.call(document.getElementsByName(name), element =>
    ['IFRAME', 'FRAME'].includes(String(element.tagName).toUpperCase()));
  const ask = (target, name, channel = null, features = null) =>
    post({type: 'codemie-hosted-open', target, name, channel, features});

  // window.open's features string, tokenized as the HTML standard does: names and values split by
  // whitespace, "=" and ",", lowercased; innerWidth/innerHeight/screenX/screenY are aliases.
  const ALIASES = {innerwidth: 'width', innerheight: 'height', screenx: 'left', screeny: 'top'};
  const separator = character => character === '=' || character === ',' || /\s/.test(character);
  const tokenize = features => {
    const text = features === undefined || features === null ? '' : String(features).toLowerCase();
    const tokens = new Map();
    let position = 0;
    const collect = wanted => {
      const start = position;
      while (position < text.length && separator(text[position]) === wanted) position++;
      return text.slice(start, position);
    };
    while (position < text.length) {
      collect(true);
      const name = collect(false);
      while (position < text.length && text[position] !== '=' && text[position] !== ',' && separator(text[position])) {
        position++;
      }
      let value = '';
      if (position < text.length && text[position] !== ',' && separator(text[position])) {
        while (position < text.length && text[position] !== ',' && separator(text[position])) position++;
        value = collect(false);
      }
      if (name) tokens.set(ALIASES[name] || name, value);
    }
    return tokens;
  };
  const truthy = value => value === '' || value === 'yes' || value === 'true' ||
    (/^[-+]?\d/.test(value) && Number.parseInt(value, 10) !== 0);
  const flag = (tokens, key, fallback = false) => tokens.has(key) ? truthy(tokens.get(key)) : fallback;
  // The standard's "is a popup window requested" check. noopener and noreferrer do not count.
  const popup = tokens => {
    if (tokens.size === 0) return false;
    if (tokens.has('popup')) return flag(tokens, 'popup');
    if (!flag(tokens, 'location') && !flag(tokens, 'toolbar')) return true;
    return !(flag(tokens, 'menubar') && flag(tokens, 'resizable', true) &&
      flag(tokens, 'scrollbars') && flag(tokens, 'status'));
  };
  const LIMITS = {width: [100, 10000], height: [100, 10000], left: [0, 10000], top: [0, 10000]};
  // What CodeMie may honour: popup and the four sizes, integers only, clamped. null for nothing.
  const windowFeatures = tokens => {
    const kept = new Map([...tokens].filter(([key]) => key !== 'noopener' && key !== 'noreferrer'));
    const result = {};
    if (popup(kept)) result.popup = true;
    for (const [key, [low, high]] of Object.entries(LIMITS)) {
      const value = kept.get(key);
      if (value !== undefined && /^[-+]?\d+$/.test(value)) {
        result[key] = Math.min(high, Math.max(low, Number.parseInt(value, 10)));
      }
    }
    return Object.keys(result).length ? result : null;
  };

  // The relay. Messages are {channel, from: 'opener' | 'child', data} or {channel, from, state}.
  let relay = null;
  try {
    relay = new BroadcastChannel('codemie-hosted-windows');
  } catch {
    // No BroadcastChannel: windows still open, but cannot talk to each other.
  }
  const send = (channel, from, fields) => {
    if (relay) relay.postMessage({channel, from, ...fields});
  };
  // This window's side of each channel: the stand-in for the other window and whether it closed.
  const peers = new Map();
  let warned = false;
  const sameOrigin = targetOrigin => {
    if (targetOrigin === '*' || targetOrigin === '/') return true;
    try {
      return new URL(targetOrigin).origin === location.origin;
    } catch {
      // Not an origin: the browser would throw; the relay drops the message.
      return false;
    }
  };
  // A stand-in, not a window: `closed`, close(), focus(), blur() and postMessage() only.
  const standIn = (channel, from) => {
    const peer = {from, other: from === 'opener' ? 'child' : 'opener', closed: false};
    peer.window = {
      get closed() {
        return peer.closed;
      },
      close() {},
      focus() {},
      blur() {},
      postMessage(data, options, transfer) {
        let targetOrigin = '/';
        let list = transfer;
        if (typeof options === 'string') {
          targetOrigin = options;
        } else if (options && typeof options === 'object') {
          targetOrigin = options.targetOrigin ?? '/';
          list = options.transfer;
        }
        if (!sameOrigin(String(targetOrigin))) return;
        if (list?.length && !warned) {
          warned = true;
          console.warn('CodeMie: a transfer list cannot reach a window opened through CodeMie and was dropped.');
        }
        send(channel, from, {data});
      },
    };
    peers.set(channel, peer);
    return peer;
  };
  const newChannel = () => typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');

  if (relay) {
    relay.addEventListener('message', event => {
      const message = event.data;
      const peer = typeof message?.channel === 'string' ? peers.get(message.channel) : undefined;
      if (!peer || message.from !== peer.other) return;
      if (message.state === 'open' || message.state === 'closed') {
        peer.closed = message.state === 'closed';
        return;
      }
      if (!('data' in message)) return;
      const relayed = new MessageEvent('message', {data: message.data, origin: location.origin});
      // MessageEvent accepts only a real window as its source; `e.source === w` must still hold.
      Object.defineProperty(relayed, 'source', {value: peer.window});
      dispatchEvent(relayed);
    });
  }
  // Each side tells the other when its page goes away or comes back (also from the back-forward cache).
  const announce = state => {
    for (const [channel, peer] of peers) send(channel, peer.from, {state});
  };
  addEventListener('pageshow', () => announce('open'));
  addEventListener('pagehide', () => announce('closed'));

  // The page CodeMie opened for a stand-in. The CodeMie page names the content frame
  // codemie-hosted-<32 hex>, or codemie-hosted-<32 hex>-opener-<channel> for a window it opened for
  // a stand-in. The name is copied into this tab's sessionStorage on every page load, so a later
  // page keeps its opener after the app renames its window; a plain CodeMie name clears it. The
  // tab's sessionStorage is never another window's, so the main window never takes the channel of
  // a window it opened.
  const OPENER_KEY = 'codemie-hosted-opener';
  const frameName = typeof window.name === 'string' ? window.name : '';
  const openedFor = /^codemie-hosted-[0-9a-f]{32}-opener-([A-Za-z0-9-]{8,64})$/.exec(frameName);
  let openerChannel = null;
  try {
    if (openedFor) {
      sessionStorage.setItem(OPENER_KEY, openedFor[1]);
    } else if (/^codemie-hosted-[0-9a-f]{32}$/.test(frameName)) {
      sessionStorage.removeItem(OPENER_KEY);
    }
    openerChannel = sessionStorage.getItem(OPENER_KEY);
  } catch {
    // Storage unavailable: only the frame name can name the opener.
    openerChannel = openedFor ? openedFor[1] : null;
  }
  let ownChannel = null;
  if (relay && window.opener === null && /^[A-Za-z0-9-]{8,64}$/.test(openerChannel ?? '')) {
    // A script cannot close its opener, so close() does nothing here.
    window.opener = standIn(openerChannel, 'child').window;
    ownChannel = openerChannel;
  }

  // window.close() on a frame does nothing, so ask the CodeMie page to close this window instead.
  // CodeMie closes only a window it opened itself and ignores the request otherwise. The opener, if
  // any, learns at once that this page is gone.
  window.close = function() {
    if (ownChannel !== null) send(ownChannel, 'child', {state: 'closed'});
    post({type: 'codemie-hosted-close-self'});
  };

  // Named windows keep their stand-in and channel, as the browser keeps the window for a name.
  const named = new Map();
  const native = window.open;
  window.open = function(url, name, features) {
    const text = url === undefined || url === null ? '' : String(url);
    const windowNamed = windowName(name);
    const frame = frameLocal(name) || (windowNamed !== '' && inPage(windowNamed));
    const target = text === '' || text === 'about:blank' || frame ? null : local(text);
    if (target === null) return Reflect.apply(native, this, arguments);
    const tokens = tokenize(features);
    const shape = windowFeatures(tokens);
    if (flag(tokens, 'noopener') || flag(tokens, 'noreferrer')) {
      ask(target, windowNamed, null, shape);
      return null;
    }
    let entry = windowNamed ? named.get(windowNamed) : undefined;
    if (entry === undefined) {
      const channel = newChannel();
      const opened = {channel, peer: standIn(channel, 'opener')};
      // close() asks CodeMie to close the window it opened; focus() on a named window asks again,
      // so CodeMie focuses that window or reopens it after the user closed it.
      opened.peer.window.close = () => {
        post({type: 'codemie-hosted-close', channel});
        opened.peer.closed = true;
      };
      opened.peer.window.focus = () => {
        if (windowNamed) ask(opened.target, windowNamed, channel, opened.features);
      };
      if (windowNamed) named.set(windowNamed, opened);
      entry = opened;
    }
    entry.target = target;
    entry.features = shape;
    entry.peer.closed = false;
    ask(target, windowNamed, entry.channel, shape);
    return entry.peer.window;
  };

  const follow = event => {
    if (event.defaultPrevented) return;
    const middle = event.type === 'auxclick' && event.button === 1;
    if (event.type === 'auxclick' ? !middle : event.button !== 0) return;
    const link = event.target instanceof Element ? event.target.closest('a[href]') : null;
    if (!link || typeof link.href !== 'string' || link.hasAttribute('download')) return;
    const frame = link.getAttribute('target') || '';
    const modified = middle || event.ctrlKey || event.metaKey || event.shiftKey;
    // A new tab or window: a modifier or middle click (always a new window), target="_blank" or any
    // other "_" name except _self, _parent and _top (a new window), or a named target such as
    // target="preview" (that named window) unless a frame of this page has that name. A link
    // returns no window, so it has no relay channel.
    const linkNamed = modified ? '' : windowName(frame);
    if (!modified && (frame === '' || frameLocal(frame) || (linkNamed !== '' && inPage(linkNamed)))) return;
    const target = local(link.href);
    if (target === null) return;
    event.preventDefault();
    ask(target, linkNamed);
  };

  // Bubble phase on window: the page's own click handlers run first, and a click they cancelled
  // is left alone.
  addEventListener('click', follow);
  addEventListener('auxclick', follow);
}
