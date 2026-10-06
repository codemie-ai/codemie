#!/bin/sh
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

set -e

# Select the customer config source. /app/external-config is an optional directory
# mount (standalone/docker-compose.standalone.yml, CODEMIE_EXTERNAL_CONFIG_DIR). When it
# contains customer-config.yaml, that file fully replaces the bundled one — no merge.
# Otherwise the config baked into the image at build time is used as-is.
#
# customer-config.yaml is always reset from the pristine image-bundled .customer-config.bundled.yaml copy
# first (Dockerfile), then the external file is re-applied if still present. This makes source
# selection idempotent across restarts of the same (not recreated) container: if the external
# file is later removed and the container is restarted without `up -d`/`run` recreating it, the
# previous external content does not linger — the bundled config is correctly restored.
EXTERNAL_CUSTOMER_CONFIG=/app/external-config/customer-config.yaml
BUNDLED_CUSTOMER_CONFIG=/app/backend/config/customer/customer-config.yaml
BUNDLED_CUSTOMER_CONFIG_PRISTINE=/app/backend/config/customer/.customer-config.bundled.yaml
cp "$BUNDLED_CUSTOMER_CONFIG_PRISTINE" "$BUNDLED_CUSTOMER_CONFIG"
if [ -f "$EXTERNAL_CUSTOMER_CONFIG" ]; then
    cp "$EXTERNAL_CUSTOMER_CONFIG" "$BUNDLED_CUSTOMER_CONFIG"
    echo "customer config: using external file from $EXTERNAL_CUSTOMER_CONFIG"
else
    echo "customer config: using bundled file (no customer-config.yaml found under /app/external-config)"
fi

# Generate /usr/share/nginx/html/config.js from environment variables.
# Python json.dumps() correctly encodes all values regardless of special characters.
python3 - <<'EOF'
import json, os

defaults = {
    "VITE_API_URL":                  "/api",
    "VITE_ENV":                      "local",
    "VITE_APP_VERSION":              "",
    "VITE_BANNER_MESSAGE":           "",
    "VITE_BANNER_LINK_LABEL":        "",
    "VITE_BANNER_LINK_ROUTE":        "",
    "VITE_CAN_SWITCH_DESIGN":        "",
    "VITE_SHOW_ALL_PROJECTS":        "false",
    "VITE_IS_EXTERNAL_LOGIN":        "false",
    "VITE_ENABLE_USER_MANAGEMENT":   "true",
    "VITE_ENABLE_BUDGET_MANAGEMENT": "false",
    "VITE_IDP_PROVIDER":             "local",
    "VITE_MCP_AUTH_ORIGIN":          "http://localhost:8080",
}

lines = ["window._env_ = window._env_ || {}"]
for key, default in defaults.items():
    # Prefer VITE_X; fall back to the unprefixed backend var (e.g. IDP_PROVIDER),
    # then the built-in default. This lets a single .env entry drive both sides.
    fallback = key[len("VITE_"):]
    value = next((os.environ[k] for k in (key, fallback) if k in os.environ), default)
    lines.append(f"window._env_.{key} = {json.dumps(value)}")

with open("/usr/share/nginx/html/config.js", "w") as f:
    f.write("\n".join(lines) + "\n")
EOF

# Start uvicorn in the background.
# PYTHONPATH=/app/backend/src is set in the Dockerfile ENV so the source-layout
# codemie package is importable without being pip-installed.
# Set UVICORN_RELOAD=true (e.g. in docker-compose.yml) to enable hot-reload
# for development. --reload and --workers are mutually exclusive in uvicorn.
cd /app/backend
if [ "${UVICORN_RELOAD:-false}" = "true" ]; then
    .venv/bin/uvicorn codemie.rest_api.main:app \
        --host 0.0.0.0 --port 8000 --reload &
else
    .venv/bin/uvicorn codemie.rest_api.main:app \
        --host 0.0.0.0 --port 8000 --workers 1 &
fi
UVICORN_PID=$!

# Poll uvicorn's healthcheck until it is ready (max 60s).
i=0
until curl -sf http://localhost:8000/v1/healthcheck >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "ERROR: uvicorn did not become healthy within 60s" >&2
        kill "$UVICORN_PID" 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

# Replace this shell with nginx as PID 1.
exec nginx -g "daemon off;"
