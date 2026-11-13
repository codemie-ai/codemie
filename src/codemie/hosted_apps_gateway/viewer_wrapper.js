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

// Own-tab w- wrapper: frames the matching v- version origin and mirrors the child's route hints
// into its own address bar. Its CSP authorizes it by the SHA-256 of this file's exact bytes.
(() => {
  const frame = document.querySelector('iframe');
  const content = new URL(frame.dataset.src);
  // A backslash or a control character (U+0000-U+001F, U+007F) is never a route hint.
  const unsafe = text => {
    for (let index = 0; index < text.length; index++) {
      const code = text.charCodeAt(index);
      if (code <= 0x1f || code === 0x7f || code === 0x5c) return true;
    }
    return false;
  };
  addEventListener('message', event => {
    if (event.source !== frame.contentWindow || event.origin !== content.origin) return;
    const data = event.data;
    if (!data || data.type !== 'codemie-hosted-location' || typeof data.target !== 'string') return;
    const target = data.target;
    if (target.length > 12288 || !target.startsWith('/') || target.startsWith('//') ||
        unsafe(target)) return;
    const url = new URL(target, location.origin);
    if (url.origin !== location.origin || url.username || url.password ||
        /^\/_platform(?:\/|$)/i.test(url.pathname)) return;
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  });
  frame.src = content.href + location.hash;
})();
