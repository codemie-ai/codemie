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

"""IAM tokens for PostgreSQL connections."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine

from codemie.clients import postgres


@pytest.fixture
def rds():
    client = MagicMock()
    client.generate_db_auth_token.return_value = "rds-token"
    with (
        patch.object(postgres.config, "PG_IAM_AUTH_PROVIDER", "aws"),
        patch.object(postgres.config, "PG_AWS_RDS_REGION", "eu-west-1"),
        patch.object(postgres.config, "POSTGRES_HOST", "app-db.example"),
        patch.object(postgres.config, "POSTGRES_PORT", 5432),
        patch.object(postgres.config, "POSTGRES_USER", "app"),
        patch("boto3.client", return_value=client),
    ):
        yield client


def test_aws_tokens_are_minted_for_the_application_database_by_default(rds):
    assert postgres._get_iam_token() == "rds-token"

    rds.generate_db_auth_token.assert_called_once_with(
        DBHostname="app-db.example", Port=5432, DBUsername="app", Region="eu-west-1"
    )


def test_aws_tokens_can_be_minted_for_another_database(rds):
    assert postgres._get_iam_token(host="analytics.example", port=6432, user="analytics") == "rds-token"

    rds.generate_db_auth_token.assert_called_once_with(
        DBHostname="analytics.example", Port=6432, DBUsername="analytics", Region="eu-west-1"
    )


def test_tokens_not_bound_to_a_server_ignore_the_endpoint():
    gcp = MagicMock(return_value="gcp-token")
    with (
        patch.object(postgres.config, "PG_IAM_AUTH_PROVIDER", "gcp"),
        patch.dict(postgres._IAM_TOKEN_PROVIDERS, {"gcp": gcp}),
    ):
        assert postgres._get_iam_token(host="analytics.example", port=6432, user="analytics") == "gcp-token"

    gcp.assert_called_once_with()


def test_an_engine_can_authenticate_to_another_database(rds):
    engine = create_engine("sqlite://")
    postgres._register_iam_token_event(engine, host="analytics.example", port=6432, user="analytics")

    cparams: dict = {}
    for listener in engine.dialect.dispatch.do_connect:  # what SQLAlchemy runs before each connect
        listener(engine.dialect, None, [], cparams)

    assert cparams["password"] == "rds-token"
    rds.generate_db_auth_token.assert_called_once_with(
        DBHostname="analytics.example", Port=6432, DBUsername="analytics", Region="eu-west-1"
    )
