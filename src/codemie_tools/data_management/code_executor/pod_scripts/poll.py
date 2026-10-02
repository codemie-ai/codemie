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

# Runs on the pod under python3, sent as `python3 -c <text> <args>`. argv: <exchange dir> <cap bytes> <request prefix>
# <request suffix>. Each request is read to cap+1 bytes so an oversize one is detected without loading it, and the
# whole poll is answered with a single JSON document on stdout.

import json
import os
import sys


def main(argv: list[str]) -> int:
    directory = argv[1]
    cap = int(argv[2])
    prefix = argv[3]
    suffix = argv[4]
    items: list[dict[str, object]] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for name in names:
        if not name.startswith(prefix) or not name.endswith(suffix):
            continue
        try:
            with open(os.path.join(directory, name), "rb") as handle:
                data = handle.read(cap + 1)
        except OSError:
            continue
        if len(data) > cap:
            items.append({"name": name, "oversize": True})
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            items.append({"name": name, "oversize": False, "bad_encoding": True})
            continue
        items.append({"name": name, "oversize": False, "body": text})
    sys.stdout.write(json.dumps(items))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
