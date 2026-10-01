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

from __future__ import annotations

from unittest.mock import patch

import psycopg2.extensions
import pytest
from asyncpg import connect_utils as asyncpg_connect_utils
from sqlalchemy.dialects.postgresql.psycopg2 import PGDialect_psycopg2
from sqlalchemy.engine import make_url

from codemie.configs.config import Config
from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError
from codemie.repository.cli_analytics.postgres import settings as settings_module
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings


def _config(**overrides) -> Config:
    base = {
        "CLI_ANALYTICS_PG_URL": "",
        "PG_URL": "",
        "PG_IAM_AUTH_PROVIDER": "",
        "POSTGRES_HOST": "db.local",
        "POSTGRES_PORT": 5433,
        "POSTGRES_DB": "codemie",
        "POSTGRES_USER": "app",
        "POSTGRES_PASSWORD": "pw",
    }
    base.update(overrides)
    return Config(**base)


def test_dedicated_analytics_url_wins():
    s = AnalyticsPgSettings.from_config(
        _config(CLI_ANALYTICS_PG_URL="postgresql://a:b@analytics/an", PG_URL="postgresql://x:y@app/app")
    )

    assert s.dsn == "postgresql://a:b@analytics/an"


def test_application_url_is_the_fallback():
    s = AnalyticsPgSettings.from_config(_config(PG_URL="postgresql://x:y@app:5432/app?sslmode=require"))

    assert s.dsn == "postgresql://x:y@app:5432/app?sslmode=require"


@pytest.mark.parametrize(
    "url",
    ["postgresql+asyncpg://u:p@h/d", "postgresql+psycopg2://u:p@h/d", "postgres://u:p@h/d", "postgresql://u:p@h/d"],
)
def test_driver_suffixes_are_normalised_for_asyncpg(url):
    assert AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=url)).dsn == "postgresql://u:p@h/d"


def test_postgres_settings_build_the_dsn_with_quoting():
    s = AnalyticsPgSettings.from_config(_config(POSTGRES_USER="me@corp", POSTGRES_PASSWORD="p@ss:/w"))

    assert s.dsn == "postgresql://me%40corp:p%40ss%3A%2Fw@db.local:5433/codemie"
    assert s.iam_auth is False


def test_iam_auth_leaves_the_password_to_a_token_callable():
    s = AnalyticsPgSettings.from_config(_config(PG_IAM_AUTH_PROVIDER="aws"))

    assert s.dsn == "postgresql://app@db.local:5433/codemie?sslmode=require"
    assert s.iam_auth is True


def test_iam_auth_applies_to_an_analytics_url_without_a_password():
    # PostgresClient injects the IAM token whatever URL is configured; the analytics pool matches it.
    s = AnalyticsPgSettings.from_config(_config(PG_IAM_AUTH_PROVIDER="gcp", CLI_ANALYTICS_PG_URL="postgresql://u@h/d"))

    assert s.iam_auth is True


@pytest.mark.parametrize(
    ("query", "libpq_query"),
    [
        ("ssl=require", "sslmode=require"),
        ("ssl=true", "sslmode=require"),
        ("ssl=false", "sslmode=disable"),
        ("ssl=verify-full&target_session_attrs=read-write", "sslmode=verify-full&target_session_attrs=read-write"),
        ("sslmode=verify-ca", "sslmode=verify-ca"),
        ("sslmode=require&ssl=false", "sslmode=require"),  # an explicit sslmode wins
        ("sslrootcert=%2Fetc%2Fca%20bundle.pem&ssl=require", "sslrootcert=%2Fetc%2Fca%20bundle.pem&sslmode=require"),
    ],
)
def test_sqlalchemy_asyncpg_ssl_parameter_becomes_libpq_sslmode(query, libpq_query):
    # asyncpg's DSN parser and libpq (psycopg2, for migrations) know only sslmode; they would
    # send ssl to the server as a setting, or reject the URL.
    s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=f"postgresql+asyncpg://u:p@h/d?{query}"))

    assert s.dsn == f"postgresql://u:p@h/d?{libpq_query}"
    assert s.sqlalchemy_url == f"postgresql+psycopg2://u:p@h/d?{libpq_query}"


def test_each_driver_gets_the_parameters_it_reads_and_the_rest_are_named_in_a_warning():
    # asyncpg sends a parameter it does not know to the server as a setting, and libpq (the
    # migrations) rejects one it does not know: either would break every connection.
    url = (
        "postgresql+asyncpg://u:p@h/d?sslmode=require&prepared_statement_cache_size=0"
        "&target_session_attrs=read-write&connect_timeout=5&sslrootcert=%2Fca.pem&channel_binding=prefer"
    )
    with patch.object(settings_module, "logger") as logger:
        s = AnalyticsPgSettings.from_config(_config(PG_URL=url))

    assert s.dsn == "postgresql://u:p@h/d?sslmode=require&target_session_attrs=read-write&sslrootcert=%2Fca.pem"
    libpq = (
        "sslmode=require&target_session_attrs=read-write&connect_timeout=5&sslrootcert=%2Fca.pem&channel_binding=prefer"
    )
    assert s.sqlalchemy_url == f"postgresql+psycopg2://u:p@h/d?{libpq}"  # libpq keeps its own, security ones included
    logged = str(logger.warning.call_args_list)
    assert all(name in logged for name in ("connect_timeout", "channel_binding")) and "1 parameter" in logged
    assert "migrations only" in logged and "prepared_statement_cache_size" not in logged
    assert "=5" not in logged and "prefer" not in logged  # names only: values may be secrets


@pytest.mark.parametrize("param", ["options=-c%20role%3Dother", "load_balance_hosts=random", "replication=database"])
def test_a_parameter_changing_what_a_connection_is_leaves_both_connections(param):
    # The pool and the migrations must reach the same server as the same role.
    with patch.object(settings_module, "logger") as logger:
        s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=f"postgresql://u:p@h/d?{param}"))

    assert (s.dsn, s.sqlalchemy_url) == ("postgresql://u:p@h/d", "postgresql+psycopg2://u:p@h/d")
    assert param.partition("=")[0] in str(logger.warning.call_args_list)


@pytest.mark.parametrize("param", ["hostaddr=10.0.0.5", "service=analytics"])
def test_a_parameter_choosing_the_server_asyncpg_cannot_apply_refuses_the_url(param):
    with pytest.raises(CliAnalyticsStorageConfigError, match=param.partition("=")[0]):
        AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=f"postgresql://u:p@h/d?{param}"))


@pytest.mark.parametrize(
    "param", ["SSLMODE=verify-full", "sslMode=require", "SSL=true", "sslmode%20=verify-full", " sslrootcert=%2Fca.pem"]
)
def test_a_miscased_or_padded_parameter_name_refuses_the_url_instead_of_weakening_tls(param):
    # Dropped, it would leave both connections on sslmode=prefer.
    with pytest.raises(CliAnalyticsStorageConfigError, match="lowercase"):
        AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=f"postgresql://u:p@h/d?{param}"))


@pytest.mark.parametrize(
    "param",
    ["channel_binding=require", "require_auth=scram-sha-256", "sslcrldir=%2Fcrl", "gssencmode=require"]
    + ["sslcertmode=require", "requirepeer=postgres"],
)
def test_a_security_setting_asyncpg_cannot_apply_refuses_the_url(param):
    # Dropped, it would leave the analytics connections weaker than the URL asks for.
    with pytest.raises(CliAnalyticsStorageConfigError, match=param.partition("=")[0]):
        AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=f"postgresql://u:p@h/d?{param}"))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@zq9@h/d",  # asyncpg and SQLAlchemy dial "zq9@h", urllib (IAM) "h"
        "postgresql://kw7@contoso.com@db/x",  # asyncpg logs in as "kw7" at "contoso.com@db"
        "postgresql://u:#zq9@db/d",
        "postgresql://u:12/zq9@h/d",
        "postgresql://u:pa?zq9@db/x",
        "postgresql://u:s3cr3t@h:x9/d",
        "postgresql://u:s3cr3t@h1:5432,h2:5432/d",
        "postgresql://u:s3cr3t@h1,h2/d",
        "postgresql://u:s3cr3t@h1,h2:5433/d",
        "postgresql://u:s3cr3t@/d?host=h1,h2",
        "postgresql://u:s3cr3t@/d?host=h1&host=h2",
        "postgresql://u:s3cr3t@h/d?user=a&user=b",
        "postgresql://u:s3cr3t@h/d?sslmode=require&zq9",  # a password's tail after a raw "&"
        "postgresql://u:5432?x=zq9@h/d",  # libpq reads the password "5432?x=zq9", others a port and a parameter
        "postgresql://h?user=zq9@corp.com",  # libpq reads the user "h?user=zq9" at "corp.com"
        "postgresql://u:100%zq9@h/d",  # a "%" that is not an escape: libpq refuses it, the others keep it
        "postgresql://u:s3cr3t@h%C3%A9zq9/d",  # SQLAlchemy and IAM would not decode the host
        "postgresql://u:s3cr3t@[fe80::1%25zq9]/d",
        "postgresql://u:s3cr3t@/d?host=%2Fzq9,%2Ftmp",  # two socket directories
        "postgresql://u:s3cr3t@/d?host=db:zq9",
        "postgresql://u:s3cr3t@/d?host=db:5432",  # libpq: one host "db:5432"; SQLAlchemy: "db", port 5432
        "postgresql://svc/team:s3cr3t@db/x",  # a raw "/" in the user name: the password would be a database name
        "postgresql://u:s3cr3t@h/d\n",  # a secret file's trailing newline, which urllib drops and libpq keeps
        "postgresql://u:s3cr3t\t@h/d",
        "postgresql://u:s3cr3t%00zq9@h/d",  # libpq refuses a NUL
        # A raw "/" then "?" in a password: its fragments would become a port, a database and a parameter.
        "postgresql://u:12/zq9?a=b@h/d",
        "postgresql://u:/zq9?a=b@h/d",
        "postgresql://svc/team:s3?c=zq9@db/x",
        "postgresql://u:12/zq9?user=b@h/d",
        "postgresql://h/d?user=zq9@corp.com",  # "@" separates the user only before the host: write %40
        "postgresql://u:s3cr3t@h/d?sslrootcert=%2Fc%2F#1.pem&sslmode=verify-full",  # urllib cuts at "#"
    ],
)
def test_a_url_the_clients_would_read_differently_is_refused_without_quoting_it(url):
    # asyncpg, SQLAlchemy (the migrations) and urllib (IAM) each split a URL their own way: they
    # must agree on the user, password, host and port, or the connections differ and the drivers'
    # errors quote the password's tail as a host name.
    with patch.object(settings_module, "logger") as logger, pytest.raises(CliAnalyticsStorageConfigError) as refused:
        AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=url))

    said = f"{refused.value} {logger.mock_calls}"
    assert refused.value.__suppress_context__ or refused.value.__context__ is None
    assert not any(fragment in said for fragment in ("zq9", "kw7", "s3cr3t", "x9", "contoso"))


@pytest.mark.parametrize(
    ("url", "dsn"),
    [
        ("postgresql://h/d?user=alice%40corp.com", "postgresql://alice%40corp.com@h/d"),
        ("postgresql://u:p@h/analytics%2Dprod", "postgresql://u:p@h/analytics-prod"),
        ("postgresql://u:p@h/code%20mie", "postgresql://u:p@h?dbname=code%20mie"),  # SQLAlchemy keeps a path's escapes
        ("postgresql://u:p%40ss@h:5432/d", "postgresql://u:p%40ss@h:5432/d"),
        ("postgresql://u:p@[::1]:5432/d", "postgresql://u:p@[::1]:5432/d"),
        ("postgresql:///d?host=%2Fvar%2Frun%2Fpostgresql", "postgresql:///d?host=%2Fvar%2Frun%2Fpostgresql"),
        # libpq's socket form in the host part, decoded as asyncpg decodes it
        ("postgresql://u:p@%2Fvar%2Frun%2Fpostgresql/d", "postgresql://u:p@/d?host=%2Fvar%2Frun%2Fpostgresql"),
        # parts given as parameters, where the URL leaves them out, reach both drivers alike
        ("postgresql://u:p@h/d?port=6432", "postgresql://u:p@h:6432/d"),
        ("postgresql://u:p@h/?dbname=x", "postgresql://u:p@h/x"),
        ("postgresql://u:p@:6432/d?host=h", "postgresql://u:p@h:6432/d"),
        ("postgresql://u@h/d?password=Xk3", "postgresql://u:Xk3@h/d"),
    ],
)
def test_one_reading_of_the_url_reaches_every_client(url, dsn):
    # The URL is read once and rebuilt fully percent-encoded for each driver, so asyncpg, the
    # migrations (SQLAlchemy/libpq) and IAM cannot read another user, password, host or port.
    s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=url))

    assert s.dsn == dsn
    assert s.sqlalchemy_url == dsn.replace("postgresql://", "postgresql+psycopg2://", 1)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@h/d?user=other",
        "postgresql://u:p@h/d?password=other",
        "postgresql://u:p@h/d?dbname=other",
        "postgresql://u:p@h:5432/d?port=6432",
        "postgresql://u:p@h/d?host=other",
        "postgresql://u:p@h/d?port=abc",
        "postgresql://u:p@h/?database=x",  # asyncpg's name for dbname, which libpq refuses
        "postgresql://u:p@h/?Database=x",  # dropped, both would open the user's default database
        "postgresql://u:p@h/?database%20=x",
    ],
)
def test_a_connection_part_set_twice_or_unreadable_refuses_the_url(url):
    # asyncpg keeps the URL's value and the migrations the parameter's: two roles, or two databases.
    with pytest.raises(CliAnalyticsStorageConfigError):
        AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=url))


@pytest.mark.parametrize(
    "overrides",
    [
        {"CLI_ANALYTICS_PG_URL": "postgresql://u:p@:6432/d"},  # asyncpg dials TCP "" (localhost), libpq its socket
        {"CLI_ANALYTICS_PG_URL": "postgresql:///d?port=6432"},  # asyncpg cannot pair one port with its 5 default hosts
        # asyncpg tries four socket directories, then TCP localhost; libpq one socket directory
        {"CLI_ANALYTICS_PG_URL": "postgresql://u:p@/d"},
        {"CLI_ANALYTICS_PG_URL": "postgresql:///d"},
        {"CLI_ANALYTICS_PG_URL": "postgresql://an@/an", "PG_IAM_AUTH_PROVIDER": "gcp"},
        {"PG_URL": "postgresql:///app"},
        {"POSTGRES_HOST": ""},
    ],
)
def test_a_connection_without_a_host_refuses_the_storage(overrides):
    with pytest.raises(CliAnalyticsStorageConfigError, match="no host"):
        AnalyticsPgSettings.from_config(_config(**overrides))


@pytest.mark.parametrize(
    ("query", "count"), [("prepared_statement_cache_size=0", "1 parameter"), ("zq9=1&zq9=2", "2 parameter")]
)
def test_unknown_parameters_are_counted_never_named(query, count):
    with patch.object(settings_module, "logger") as logger:
        s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL=f"postgresql://u@h/d?sslmode=require&{query}"))

    assert s.dsn == "postgresql://u@h/d?sslmode=require"
    logged = str(logger.warning.call_args_list)
    assert query.partition("=")[0] not in logged and count in logged


def test_parameter_names_are_percent_decoded_as_libpq_decodes_them():
    # Left encoded, sslmode would be an unknown name, dropped: the connections would fall back to prefer.
    s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL="postgresql://u@h/d?ss%6Cmode=verify-full"))

    assert s.dsn == "postgresql://u@h/d?sslmode=verify-full"


@pytest.mark.parametrize(
    ("url", "endpoint"),
    [
        ("postgresql://an@an.example/an?port=6432", ("an.example", 6432, "an")),
        ("postgresql://an.example/an?user=svc%40an", ("an.example", 5432, "svc@an")),
        ("postgresql://an.example/an?user=svc&port=6432", ("an.example", 6432, "svc")),
    ],
)
def test_iam_tokens_are_minted_for_parts_given_as_parameters_too(url, endpoint):
    s = AnalyticsPgSettings.from_config(_config(PG_IAM_AUTH_PROVIDER="aws", CLI_ANALYTICS_PG_URL=url))

    assert (s.iam_auth, s.iam_endpoint) == (True, endpoint)


def test_a_password_given_as_a_parameter_is_kept_when_the_application_uses_iam():
    s = AnalyticsPgSettings.from_config(
        _config(PG_IAM_AUTH_PROVIDER="aws", CLI_ANALYTICS_PG_URL="postgresql://an@an.example/an?password=pw")
    )

    assert s.iam_auth is False


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://an@/an?host=%2Fvar%2Frun%2Fpostgresql",  # the token would be signed for POSTGRES_HOST
        "postgresql://an.example/an",  # ... for POSTGRES_USER, while the drivers log in as PGUSER or the OS user
    ],
)
def test_aws_iam_needs_a_network_host_and_a_user(url):
    with pytest.raises(CliAnalyticsStorageConfigError, match="IAM"):
        AnalyticsPgSettings.from_config(_config(PG_IAM_AUTH_PROVIDER="aws", CLI_ANALYTICS_PG_URL=url))


@pytest.mark.parametrize("provider", ["gcp", "azure"])
@pytest.mark.parametrize("url", ["postgresql://an@/an?host=%2Fcloudsql%2Fp%3Ar%3Ai", "postgresql://an.example/an"])
def test_gcp_and_azure_tokens_are_bound_to_no_endpoint_so_any_url_takes_them(provider, url):
    # A Cloud SQL Auth Proxy socket, say: their tokens are signed for no host, port or user.
    s = AnalyticsPgSettings.from_config(_config(PG_IAM_AUTH_PROVIDER=provider, CLI_ANALYTICS_PG_URL=url))

    assert s.iam_auth is True


def test_sqlalchemy_url_uses_the_sync_driver_and_keeps_the_query():
    s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL="postgresql://u:p@h:1/d?sslmode=require"))

    assert s.sqlalchemy_url == "postgresql+psycopg2://u:p@h:1/d?sslmode=require"


def test_server_settings_pin_schema_memory_timeouts_and_turn_jit_off():
    s = AnalyticsPgSettings.from_config(
        _config(
            CLI_ANALYTICS_PG_SCHEMA="analytics_x",
            CLI_ANALYTICS_PG_WORK_MEM="64MB",
            CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS=1234,
        )
    )

    assert s.server_settings() == {
        "search_path": "analytics_x",
        "application_name": "codemie-cli-analytics",
        "work_mem": "64MB",
        "statement_timeout": "1234",
        # Measured at x3: JIT compiled ~760 functions (one set per partition) for a 3 ms session
        # detail query and made it take ~500 ms; no endpoint got faster with it.
        "jit": "off",
    }


def test_operational_settings_are_taken_from_config():
    s = AnalyticsPgSettings.from_config(
        _config(
            CLI_ANALYTICS_PG_POOL_SIZE=3,
            CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS=250,
            CLI_ANALYTICS_RAW_RETENTION_DAYS=30,
            CLI_ANALYTICS_ROLLUP_RETENTION_DAYS=200,
            CLI_ANALYTICS_DEDUP_RETENTION_DAYS=7,
            CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS=10,
            CLI_ANALYTICS_ROLLUP_BATCH_SIZE=100,
            CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS=2,
            CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES=15,
        )
    )

    assert (s.pool_size, s.ingest_acquire_timeout_s) == (3, 0.25)
    assert (s.raw_retention_days, s.rollup_retention_days, s.dedup_retention_days) == (30, 200, 7)
    assert (s.rollup_refresh_seconds, s.rollup_batch_size) == (10, 100)
    assert (s.partition_premake_weeks, s.maintenance_interval_minutes) == (2, 15)


def test_advisory_lock_keys_are_stable_signed_64_bit_and_scoped_to_the_schema():
    a = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_SCHEMA="one"))
    b = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_SCHEMA="two"))

    assert a.lock_key("migrations") == AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_SCHEMA="one")).lock_key(
        "migrations"
    )
    assert len({a.lock_key("migrations"), a.lock_key("maintenance"), b.lock_key("migrations")}) == 3
    assert -(2**63) <= a.lock_key("migrations") < 2**63


def test_repr_never_shows_the_password():
    s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_URL="postgresql://u:secret@h/d"))

    assert "secret" not in repr(s)


def test_an_analytics_url_with_a_password_keeps_it_when_the_application_uses_iam():
    # The token would replace the URL's password.
    separate = AnalyticsPgSettings.from_config(
        _config(PG_IAM_AUTH_PROVIDER="aws", CLI_ANALYTICS_PG_URL="postgresql://a:b@analytics/an")
    )
    shared = AnalyticsPgSettings.from_config(_config(PG_IAM_AUTH_PROVIDER="aws"))
    # PostgresClient replaces the application URL's password with a token, and so does the pool.
    application = AnalyticsPgSettings.from_config(
        _config(PG_IAM_AUTH_PROVIDER="aws", PG_URL="postgresql://x:y@app/app")
    )

    assert (separate.iam_auth, shared.iam_auth, application.iam_auth) == (False, True, True)


def _asyncpg_reads(dsn: str, passfile) -> tuple:
    """Who and where the pool connects: asyncpg's own DSN parser (asyncpg 0.30)."""
    addrs, params = asyncpg_connect_utils._parse_connect_dsn_and_args(
        dsn=dsn, host=None, port=None, user=None, password=None, passfile=passfile, database=None, ssl=None,
        direct_tls=False, server_settings=None, target_session_attrs=None, krbsrvname=None, gsslib=None,
    )  # fmt: skip
    (addr,) = addrs  # one host: several would be asyncpg's own defaults
    if isinstance(addr, str):  # a socket: "<directory>/.s.PGSQL.<port>"
        directory, _, name = addr.rpartition("/")
        addr = (directory, int(name.rpartition(".")[2]))
    return params.user, params.password, *addr, params.database


def _migrations_read(url: str) -> tuple:
    """Who and where the migrations connect: SQLAlchemy's psycopg2 dialect, then libpq."""
    _, kwargs = PGDialect_psycopg2().create_connect_args(make_url(url))
    conninfo = psycopg2.extensions.parse_dsn(psycopg2.extensions.make_dsn(**kwargs))
    port = int(conninfo.get("port", 5432))
    return conninfo.get("user"), conninfo.get("password"), conninfo["host"], port, conninfo["dbname"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"CLI_ANALYTICS_PG_URL": "postgresql://u:p@h/d"}, ("u", "p", "h", 5432, "d")),
        (
            {"CLI_ANALYTICS_PG_URL": "postgresql://me%40corp:p%40ss%3A%2Fw%2Bx@db.local:5433/code%20mie"},
            ("me@corp", "p@ss:/w+x", "db.local", 5433, "code mie"),
        ),
        (
            {"CLI_ANALYTICS_PG_URL": "postgresql://h:6432/d?user=alice%40corp.com&password=p%26w%3Dx"},
            ("alice@corp.com", "p&w=x", "h", 6432, "d"),
        ),
        ({"CLI_ANALYTICS_PG_URL": "postgresql://u:p@[::1]:5433/d"}, ("u", "p", "::1", 5433, "d")),
        (
            {"CLI_ANALYTICS_PG_URL": "postgresql://u:p@%2Fvar%2Frun%2Fpostgresql:6432/d"},
            ("u", "p", "/var/run/postgresql", 6432, "d"),
        ),
        (
            {"CLI_ANALYTICS_PG_URL": "postgresql:///d?host=%2Ftmp&port=6432&user=u&password=p"},
            ("u", "p", "/tmp", 6432, "d"),
        ),
        ({"CLI_ANALYTICS_PG_URL": "postgresql://u:p@h:6432/?dbname=a%2Fb%20c"}, ("u", "p", "h", 6432, "a/b c")),
        ({"CLI_ANALYTICS_PG_URL": "postgresql://u:p@h/?dbname=a%3Fb%2541"}, ("u", "p", "h", 5432, "a?b%41")),
        ({"CLI_ANALYTICS_PG_URL": "postgresql://u:p@h/analytics%2Dprod"}, ("u", "p", "h", 5432, "analytics-prod")),
        (
            {"POSTGRES_USER": "app user", "POSTGRES_PASSWORD": "p@ss w+rd/1", "POSTGRES_DB": "code mie"},
            ("app user", "p@ss w+rd/1", "db.local", 5433, "code mie"),
        ),
    ],
)
def test_the_pool_and_the_migrations_connect_as_the_same_user_to_the_same_database(
    overrides, expected, tmp_path, monkeypatch
):
    # Read by the drivers' own parsers: SQLAlchemy passes a URL's database name on undecoded, and
    # asyncpg dials the host part's (even an empty one) before a socket given as a parameter.
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGPASSFILE"):
        monkeypatch.delenv(name, raising=False)
    s = AnalyticsPgSettings.from_config(_config(**overrides))

    assert _asyncpg_reads(s.dsn, tmp_path / "no-pgpass") == expected
    assert _migrations_read(s.sqlalchemy_url) == expected
    user, _, host, port, _ = expected
    if not host.startswith("/"):
        assert s.iam_endpoint == (host, port, user)  # an IAM token is signed for them


def test_ingest_gets_its_own_statement_timeout():
    s = AnalyticsPgSettings.from_config(_config(CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS=1500))

    assert s.ingest_statement_timeout_ms == 1500


@pytest.mark.parametrize(
    ("overrides", "endpoint"),
    [
        ({}, ("db.local", 5433, "app")),
        ({"CLI_ANALYTICS_PG_URL": "postgresql://an%40lytics@an.example:6432/an"}, ("an.example", 6432, "an@lytics")),
        ({"CLI_ANALYTICS_PG_URL": "postgresql://an@an.example/an"}, ("an.example", 5432, "an")),
    ],
)
def test_iam_tokens_are_minted_for_the_host_port_and_user_the_pool_connects_to(overrides, endpoint):
    # An AWS RDS token is signed for one endpoint and user: a separate analytics database needs its own.
    s = AnalyticsPgSettings.from_config(_config(PG_IAM_AUTH_PROVIDER="aws", **overrides))

    assert s.iam_endpoint == endpoint
