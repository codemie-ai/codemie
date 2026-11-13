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

// Route report for hosted HTML navigations. Appended inline inside an arrow function after
// `const viewer = <viewer origin>, ui = <CodeMie origins>, embedded = <bool>;` (navigation.py).
//
// Messages go only to the frame's actual parent: location.ancestorOrigins[0] when the browser
// names it (Chromium, WebKit) and it is the viewer or a CodeMie origin. When it names none (no
// ancestorOrigins, or an opaque "null" entry as Firefox reports), the gateway's choice stands:
// the CodeMie origins for an embedded launch, the viewer otherwise.
const ancestors = location.ancestorOrigins;
const named = ancestors?.length && ancestors[0] !== 'null' ? ancestors[0] : null;
let known;
if (named !== null) {
  known = [named];
} else if (embedded) {
  known = ui;
} else {
  known = [viewer];
}
const parents = parent === window
  ? []
  : known.filter(origin => origin === viewer || ui.includes(origin));

const post = message => {
  for (const origin of parents) parent.postMessage(message, origin);
};

const report = () => post({
  type: 'codemie-hosted-location',
  target: location.pathname + location.search + location.hash,
});

for (const name of ['pushState', 'replaceState']) {
  const original = history[name];
  history[name] = function(...args) {
    const value = Reflect.apply(original, this, args);
    report();
    return value;
  };
}

addEventListener('popstate', report);
addEventListener('hashchange', report);
report();
