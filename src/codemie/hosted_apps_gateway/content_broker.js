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
  const ask = (target, name) => post({type: 'codemie-hosted-open', target, name});

  const enabled = (features, key) => {
    const text = features === undefined || features === null ? '' : String(features);
    return text.toLowerCase().split(/[\s,]+/).some(token => {
      const [name, value = ''] = token.split('=');
      return name === key && (
        value === '' || value === 'yes' || value === 'true' ||
        (/^[-+]?\d/.test(value) && parseInt(value, 10) !== 0)
      );
    });
  };

  const native = window.open;
  window.open = function(url, name, features) {
    const text = url === undefined || url === null ? '' : String(url);
    const named = windowName(name);
    const frame = frameLocal(name) || (named !== '' && inPage(named));
    const target = text === '' || text === 'about:blank' || frame ? null : local(text);
    if (target === null) return Reflect.apply(native, this, arguments);
    ask(target, named);
    if (enabled(features, 'noopener') || enabled(features, 'noreferrer')) return null;
    // A stand-in, not a window: no opener link, `closed` stays false, and nothing else (print(),
    // document) is there. focus() on a named window asks again, so CodeMie focuses that window or
    // reopens it after the user closed it.
    return {
      closed: false,
      close() {},
      focus() {
        if (named) ask(target, named);
      },
      blur() {},
      postMessage() {},
    };
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
    // target="preview" (that named window) unless a frame of this page has that name.
    const named = modified ? '' : windowName(frame);
    if (!modified && (frame === '' || frameLocal(frame) || (named !== '' && inPage(named)))) return;
    const target = local(link.href);
    if (target === null) return;
    event.preventDefault();
    ask(target, named);
  };

  // Bubble phase on window: the page's own click handlers run first, and a click they cancelled
  // is left alone.
  addEventListener('click', follow);
  addEventListener('auxclick', follow);
}
