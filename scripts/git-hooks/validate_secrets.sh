#!/usr/bin/env bash
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

# Cross-platform secrets scan for the pre-commit hook.
# Ports codemie-ui/scripts/validate-secrets.mjs to bash so the backend
# (which has no Node runtime) can run gitleaks at commit time.
#
# Behaviour:
#   * Detects Docker -> Podman -> Apple Containers (macOS) in that order.
#   * Uses the first engine whose daemon is live.
#   * Scans staged changes only (`gitleaks protect --staged`).
#   * Honours .gitleaks.toml if present.
#   * Exit 1 (hard block) when no engine is available or its daemon is not
#     running, printing an actionable hint.
#   * Honours CODEMIE_PRECOMMIT_ENABLED=false/0/off to skip.

set -euo pipefail

GITLEAKS_IMAGE="ghcr.io/gitleaks/gitleaks:v8.30.1"

shopt -s nocasematch
case "${CODEMIE_PRECOMMIT_ENABLED:-true}" in
  false|0|off)
    echo "codemie-gitleaks: skipped (CODEMIE_PRECOMMIT_ENABLED=$CODEMIE_PRECOMMIT_ENABLED)"
    shopt -u nocasematch
    exit 0
    ;;
esac
shopt -u nocasematch

repo_root="$(git rev-parse --show-toplevel)"
config_path="$repo_root/.gitleaks.toml"

# Resolve git dirs (absolute). In a linked worktree, git-dir is inside
# .git/worktrees/<name> and git-common-dir is the main repo's .git/ — both
# may live outside repo_root and must be mounted so gitleaks' internal git
# calls can resolve refs.
resolve_git_dir() {
  local d
  d="$(git rev-parse "$1")"
  [[ "$d" = /* ]] || d="$(cd "$repo_root" && cd "$d" && pwd)"
  printf '%s\n' "$d"
}
git_dir="$(resolve_git_dir --git-dir)"
git_common_dir="$(resolve_git_dir --git-common-dir)"

if [[ "${OSTYPE:-}" == darwin* ]]; then
  for extra in /opt/podman/bin /opt/homebrew/bin /usr/local/bin \
               /Applications/Docker.app/Contents/Resources/bin; do
    if [[ -d "$extra" && ":$PATH:" != *":$extra:"* ]]; then
      PATH="$PATH:$extra"
    fi
  done
  export PATH
fi

have() { command -v "$1" >/dev/null 2>&1; }

# 5s cap so a wedged container socket fails fast instead of hanging the
# pre-commit hook forever. Fall back to the unwrapped call when timeout is
# unavailable (e.g. macOS without coreutils).
probe() {
  if have timeout; then
    timeout 5 "$@" >/dev/null 2>&1
  else
    "$@" >/dev/null 2>&1
  fi
}

daemon_running() {
  local engine="$1"
  have "$engine" && probe "$engine" info
}

apple_containers_running() {
  [[ "${OSTYPE:-}" == darwin* ]] && have container && probe container system status
}

detect_engine() {
  for e in docker podman; do
    if daemon_running "$e"; then echo "$e"; return 0; fi
  done
  if apple_containers_running; then echo container; return 0; fi
  # Installed-but-stopped fallback. `container` is Apple-only; including it
  # on Linux would pick up unrelated binaries (e.g. systemd-container's
  # `container`) and produce a misleading "Apple Containers daemon is not
  # running" diagnosis.
  local fallback=(docker podman)
  [[ "${OSTYPE:-}" == darwin* ]] && fallback+=(container)
  for e in "${fallback[@]}"; do
    if have "$e"; then echo "$e"; return 0; fi
  done
  echo ""
}

hint_for_stopped_daemon() {
  local engine="$1"
  case "$engine" in
    docker)
      if have colima;   then echo "Run 'colima start' to enable secrets detection locally"; return; fi
      if have orbstack; then echo "Start OrbStack to enable secrets detection locally";     return; fi
      echo "Start Docker Desktop to enable secrets detection locally"
      ;;
    podman)
      echo "Run 'podman machine start' to enable secrets detection locally"
      ;;
    container)
      echo "Start Apple Containers to enable secrets detection locally"
      ;;
    *)
      echo "Start your container engine to enable secrets detection locally"
      ;;
  esac
}

engine="$(detect_engine)"

if [[ -z "$engine" ]]; then
  echo "codemie-gitleaks: no container engine found" >&2
  echo "Install Docker, Podman, or Apple Containers to enable local secrets scanning." >&2
  echo "(Set CODEMIE_PRECOMMIT_ENABLED=false to skip the whole pre-commit hook if you must commit now.)" >&2
  exit 1
fi

if ! { [[ "$engine" == "container" ]] && apple_containers_running; } \
   && ! { [[ "$engine" != "container" ]] && daemon_running "$engine"; }; then
  case "$engine" in
    container) label="Apple Containers" ;;
    *)         label="$(tr '[:lower:]' '[:upper:]' <<<"${engine:0:1}")${engine:1}" ;;
  esac
  echo "codemie-gitleaks: $label daemon is not running" >&2
  hint_for_stopped_daemon "$engine" >&2
  echo "(Set CODEMIE_PRECOMMIT_ENABLED=false to skip the whole pre-commit hook.)" >&2
  exit 1
fi

args=(run --rm
      -v "$repo_root:/workspace" -w /workspace)

# In a linked worktree the real git metadata lives outside repo_root; mount
# the actual git-dir and git-common-dir at their host paths so container git
# can resolve .git file redirection and shared refs.
if [[ "$git_dir" != "$repo_root/.git" ]]; then
  args+=(-v "$git_dir:$git_dir")
  if [[ "$git_common_dir" != "$git_dir" ]]; then
    args+=(-v "$git_common_dir:$git_common_dir")
  fi
fi

# Configure git safe.directory via GIT_CONFIG_* env vars (git honours these;
# GIT_SAFE_DIRECTORY is not a real git variable). Wildcard covers /workspace
# and any worktree paths mounted above.
args+=(-e GIT_CONFIG_COUNT=1
       -e GIT_CONFIG_KEY_0=safe.directory
       -e GIT_CONFIG_VALUE_0=*
       "$GITLEAKS_IMAGE"
       protect --staged --no-banner --verbose --source=/workspace)

if [[ -f "$config_path" ]]; then
  args+=(--config=/workspace/.gitleaks.toml)
fi

echo "codemie-gitleaks: scanning staged changes with $engine ($GITLEAKS_IMAGE)"
set +e
"$engine" "${args[@]}"
ec=$?
set -e

case "$ec" in
  0)
    ;;
  1)
    echo "" >&2
    echo "codemie-gitleaks: secrets detected in staged changes. Remove them before committing." >&2
    echo "If this is a false positive, extend the allowlist/stopwords in .gitleaks.toml." >&2
    exit 1
    ;;
  *)
    echo "" >&2
    echo "codemie-gitleaks: scan failed to run (exit $ec) - this is NOT a secret finding." >&2
    echo "Common causes: image pull failed (offline), container OOM, or git 'dubious ownership' inside the container." >&2
    echo "Fix the environment and retry; do not bypass the secrets gate for scan-run failures." >&2
    exit "$ec"
    ;;
esac
