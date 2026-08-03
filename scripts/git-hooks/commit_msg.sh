#!/usr/bin/env bash
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

# commit-msg hook: enforce the EPMCDME-<n>: subject prefix.
#
# Invoked by git / the pre-commit framework with the path to the commit
# message file as $1. Exit 0 = accepted or intentionally bypassed;
# exit 1 = rejected.

set -euo pipefail

msg_file="${1:-}"
if [[ -z "$msg_file" || ! -f "$msg_file" ]]; then
  echo "[commit-msg] No commit message file provided; skipping."
  exit 0
fi

# Bypass while a merge, rebase reword, or cherry-pick is in progress.
if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
  exit 0
fi
git rev-parse -q --verify REBASE_HEAD >/dev/null 2>&1 && exit 0
git rev-parse -q --verify CHERRY_PICK_HEAD >/dev/null 2>&1 && exit 0

# First meaningful line (skip blank lines and comment lines).
subject=""
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  [[ "$line" == \#* ]] && continue
  subject="$line"
  break
done < "$msg_file"

# Auto-generated / autosquash subjects are allowed through unchanged.
case "$subject" in
  "Merge "* | "Revert "* | "fixup!"* | "squash!"* | "amend!"*)
    exit 0
    ;;
esac

if [[ "$subject" =~ ^EPMCDME-[0-9]+: ]]; then
  exit 0
fi

echo "[commit-msg] Commit message must start with 'EPMCDME-<number>:'."
echo "[commit-msg] Got: '$subject'"
echo "[commit-msg] Example: 'EPMCDME-13747: Move heavy checks to pre-push'"
exit 1
