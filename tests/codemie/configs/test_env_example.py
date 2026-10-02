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

from pathlib import Path

from dotenv import dotenv_values


REPO_ROOT = Path(__file__).parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Fields from the previously committed .env that must appear in .env.example.
# Kept in exact 1:1 correspondence with that file — no more, no fewer keys —
# so .env.example doesn't drift into templating every optional Config field.
REQUIRED_KEYS = {
    "ENV",
    "MODELS_ENV",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_URL",
    "AZURE_SPEECH_REGION",
    "GOOGLE_PROJECT_ID",
    "GOOGLE_REGION",
    "GOOGLE_VERTEXAI_REGION",
    "GOOGLE_CLAUDE_VERTEXAI_REGION",
    "OPENAI_API_VERSION",
    "GITLAB_IDENTIFIERS",
    "CODEMIE_PREPUSH_ENABLED",
    "LITELLM_PREMIUM_MODELS_ALIASES",
    "ENABLE_USER_MANAGEMENT",
    "IDP_PROVIDER",
    "SUPERADMIN_EMAIL",
    "SUPERADMIN_PASSWORD",
    "EMAIL_VERIFICATION_ENABLED",
    "PASSWORD_MIN_LENGTH",
    "RATE_LIMIT_LOGIN",
}


def test_env_example_exists():
    assert ENV_EXAMPLE.exists(), ".env.example must exist at the repo root"


def test_env_example_is_parseable():
    values = dotenv_values(ENV_EXAMPLE)
    assert isinstance(values, dict)
    assert len(values) > 0, ".env.example must not be empty"


def test_env_example_contains_required_keys():
    values = dotenv_values(ENV_EXAMPLE)
    missing = REQUIRED_KEYS - values.keys()
    assert not missing, f".env.example is missing required keys: {sorted(missing)}"


def test_env_example_has_no_extra_keys():
    values = dotenv_values(ENV_EXAMPLE)
    extra = values.keys() - REQUIRED_KEYS
    assert not extra, f".env.example has keys not present in the original .env: {sorted(extra)}"
