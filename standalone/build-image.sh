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
#
# Builds the standalone codemie image (backend + frontend + nginx) using named
# build contexts. Requires a builder with named-context support (BuildKit-enabled
# Docker, or Podman).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE_TAG=""
BACKEND_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_ROOT=""
ENGINE="docker"
NO_CACHE=0

usage() {
    cat <<'EOF'
Usage: build-image.sh --image-tag <tag> [options]

Required:
  --image-tag <tag>        Tag for the built image.

Options:
  --backend-root <path>    Backend repo root (default: repo containing this script).
  --frontend-root <path>   Frontend repo root (default: <backend-root>/../codemie-ui).
  --engine docker|podman   Container engine to use (default: docker).
  --no-cache               Build without using cache.
  -h, --help               Show this help.
EOF
}

require_value() {
    case "${2-}" in
        ''|--*)
            echo "ERROR: $1 requires a value." >&2
            usage >&2
            exit 1
            ;;
    esac
}

while [ $# -gt 0 ]; do
    case "$1" in
        --image-tag)      require_value "$@"; IMAGE_TAG="$2"; shift 2 ;;
        --backend-root)   require_value "$@"; BACKEND_ROOT="$2"; shift 2 ;;
        --frontend-root)  require_value "$@"; FRONTEND_ROOT="$2"; shift 2 ;;
        --engine)         require_value "$@"; ENGINE="$2"; shift 2 ;;
        --no-cache)       NO_CACHE=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
    esac
done

if [ -z "$IMAGE_TAG" ]; then
    echo "ERROR: --image-tag is required." >&2
    usage >&2
    exit 1
fi

case "$ENGINE" in
    docker|podman) ;;
    *) echo "ERROR: --engine must be 'docker' or 'podman', got '$ENGINE'." >&2; exit 1 ;;
esac

if [ ! -d "$BACKEND_ROOT" ]; then
    echo "ERROR: Backend root not found: $BACKEND_ROOT" >&2
    exit 1
fi
BACKEND_ROOT="$(cd "$BACKEND_ROOT" && pwd)"

# Default frontend root is derived from the *resolved* backend root, so an
# explicit --backend-root still yields the correct sibling default.
if [ -z "$FRONTEND_ROOT" ]; then
    FRONTEND_ROOT="${BACKEND_ROOT}/../codemie-ui"
fi
if [ ! -d "$FRONTEND_ROOT" ]; then
    echo "ERROR: Frontend root not found: $FRONTEND_ROOT" >&2
    exit 1
fi
FRONTEND_ROOT="$(cd "$FRONTEND_ROOT" && pwd)"

# 1. Validate engine binary and daemon liveness (fail fast before the build).
if ! command -v "$ENGINE" >/dev/null 2>&1; then
    echo "ERROR: Engine '$ENGINE' not found on PATH. Install Docker or Podman and ensure it is in PATH." >&2
    exit 1
fi
if ! "$ENGINE" info >/dev/null 2>&1; then
    echo "ERROR: Engine '$ENGINE' daemon is not running or not reachable." >&2
    exit 1
fi

# 2. Required packaging/source files must exist before invoking the builder.
# Resolved from BACKEND_ROOT (authoritative), not SCRIPT_DIR, so an explicit
# --backend-root pointing at a different checkout is honored.
DOCKERFILE="${BACKEND_ROOT}/standalone/Dockerfile"
required_paths=(
    "$DOCKERFILE"
    "${BACKEND_ROOT}/standalone/docker/entrypoint.sh"
    "${BACKEND_ROOT}/standalone/docker/nginx.conf"
    "${BACKEND_ROOT}/pyproject.toml"
    "${BACKEND_ROOT}/poetry.lock"
    "${BACKEND_ROOT}/src"
    "${BACKEND_ROOT}/config"
    "${FRONTEND_ROOT}/package.json"
    "${FRONTEND_ROOT}/package-lock.json"
    "${FRONTEND_ROOT}/index.html"
    "${FRONTEND_ROOT}/vite.config.ts"
    "${FRONTEND_ROOT}/tsconfig.json"
    "${FRONTEND_ROOT}/tsconfig.node.json"
    "${FRONTEND_ROOT}/postcss.config.js"
    "${FRONTEND_ROOT}/tailwind.config.ts"
)
for p in "${required_paths[@]}"; do
    if [ ! -e "$p" ]; then
        echo "ERROR: Required source not found: $p" >&2
        exit 1
    fi
done

# 3. Open timestamped log (all subsequent output is teed into it).
LOG_PATH="${SCRIPT_DIR}/build-$(date +%Y%m%d-%H%M%S).log"
log() {
    echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_PATH"
}

log "=== codemie standalone build ==="
log "Engine:       $ENGINE"
log "ImageTag:     $IMAGE_TAG"
log "BackendRoot:  $BACKEND_ROOT"
log "FrontendRoot: $FRONTEND_ROOT"
log "NoCache:      $([ "$NO_CACHE" -eq 1 ] && echo true || echo false)"
log "Log:          $LOG_PATH"

# 4. Source state — branch/SHA/dirty for backend and frontend.
log "--- Source state ---"
report_source_state() {
    local label="$1" path="$2"
    local branch sha dirty_count
    branch="$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '(unknown)')"
    sha="$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo '(unknown)')"
    if dirty_count="$(git -C "$path" status --porcelain 2>/dev/null)"; then
        if [ -n "$dirty_count" ]; then
            local n
            n="$(printf '%s\n' "$dirty_count" | wc -l)"
            log "$label: branch=$branch sha=$sha DIRTY ($n changed)"
        else
            log "$label: branch=$branch sha=$sha clean"
        fi
    else
        log "$label: branch=$branch sha=$sha (git error)"
    fi
}
report_source_state "codemie (backend)"     "$BACKEND_ROOT"
report_source_state "codemie-ui (frontend)" "$FRONTEND_ROOT"

# 5. Run build.
log "--- Build ---"
# Docker/Podman RUN steps default to a 1024 open-file limit, which is too low
# for the frontend build stage (Vite/Tailwind touch many source files
# concurrently). Raise it for the build container only.
build_args=(build --ulimit nofile=65536:65536 --build-context "frontend=${FRONTEND_ROOT}" -t "$IMAGE_TAG" -f "$DOCKERFILE")
[ "$NO_CACHE" -eq 1 ] && build_args+=(--no-cache)
[ "$ENGINE" = "podman" ] && build_args+=(--format docker)
build_args+=("$BACKEND_ROOT")

log "Command: $ENGINE ${build_args[*]}"

set +e
"$ENGINE" "${build_args[@]}" 2>&1 | tee -a "$LOG_PATH"
build_exit="${PIPESTATUS[0]}"
set -e

log "--- Build exit code: $build_exit ---"
exit "$build_exit"
