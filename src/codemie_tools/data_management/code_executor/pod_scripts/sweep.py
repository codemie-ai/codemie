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

# Removes every child of the bridge folder except this run's, dotfiles included.
# argv: <bridge dir> <keep name>.

import os
import shutil
import sys


def main(argv: list[str]) -> int:
    bridge = argv[1]
    keep = argv[2]
    try:
        names = os.listdir(bridge)
    except OSError:
        names = []
    for name in names:
        if name == keep:
            continue
        path = os.path.join(bridge, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
