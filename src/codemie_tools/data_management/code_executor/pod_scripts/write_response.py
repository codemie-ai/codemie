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

# Publishes one response from stdin and retires the request, in a single exec. argv: <tmp> <response> <request>
# <length>. The body travels over stdin because argv is limited to about 128 KiB per argument (and an exec's
# command line is sent as URL query parameters), but it is length-prefixed: the script reads exactly <length>
# bytes and exits. A v4.channel.k8s.io exec has no stdin half-close (an empty frame is not EOF), so nothing may
# wait for end of input. A short stream (EOF or a dropped connection before <length> bytes) publishes nothing
# and exits non-zero, which the channel retries; the request file stays until a response is published.

import contextlib
import os
import sys


def main(argv: list[str]) -> int:
    tmp, response, request, length = argv[1], argv[2], argv[3], int(argv[4])
    data = sys.stdin.buffer.read(length)
    if len(data) != length:
        return 2
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, response)
    with contextlib.suppress(OSError):
        os.remove(request)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
