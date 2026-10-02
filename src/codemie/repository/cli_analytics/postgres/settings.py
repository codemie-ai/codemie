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

"""Resolved settings of the PostgreSQL analytics storage."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

from codemie.configs.config import Config
from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError

logger = logging.getLogger(__name__)

APPLICATION_NAME = "codemie-cli-analytics"

_POSTGRESQL_SCHEME = "postgresql://"
_SCHEME = re.compile(r"^(?:postgresql|postgres)(?:\+[a-z0-9_]+)?://")
# SQLAlchemy's asyncpg URLs carry `ssl` (PostgresClient writes ssl=false for sslmode=disable);
# asyncpg's DSN parser and libpq know only `sslmode`.
_SSL_TO_SSLMODE = {"true": "require", "false": "disable"}
_DEFAULT_PORT = 5432


# libpq's connection parameters (PostgreSQL 17): the migrations' URL (psycopg2) keeps these,
# as libpq rejects any other.
_LIBPQ_PARAMS = frozenset(
    {
        *("host", "hostaddr", "port", "dbname", "user", "password", "passfile", "service"),
        *("require_auth", "channel_binding", "connect_timeout", "client_encoding", "options"),
        *("application_name", "fallback_application_name", "replication", "target_session_attrs"),
        *("keepalives", "keepalives_idle", "keepalives_interval", "keepalives_count", "tcp_user_timeout"),
        *("sslmode", "requiressl", "sslnegotiation", "sslcompression", "sslcert", "sslkey", "sslpassword"),
        *("sslcertmode", "sslrootcert", "sslcrl", "sslcrldir", "sslsni", "requirepeer"),
        *("ssl_min_protocol_version", "ssl_max_protocol_version", "gssencmode", "krbsrvname", "gsslib"),
        *("gssdelegation", "load_balance_hosts"),
    }
)
# The ones asyncpg's DSN parser reads (asyncpg 0.30): it sends any other to the server as a
# setting, which fails the connection, so the pool's URL keeps only these.
_ASYNCPG_PARAMS = frozenset(
    {
        *("host", "port", "dbname", "user", "password", "passfile", "target_session_attrs"),
        *("sslmode", "sslcert", "sslkey", "sslrootcert", "sslcrl", "sslpassword", "sslnegotiation"),
        *("ssl_min_protocol_version", "ssl_max_protocol_version", "krbsrvname", "gsslib"),
    }
)
# libpq parameters asyncpg cannot apply that decide which server a connection reaches: refused,
# as the pool and the migrations would otherwise reach different ones.
_TARGET_PARAMS = frozenset({"hostaddr", "service"})
# Ones changing a session's role, settings or kind: left out of both connections alike.
_SESSION_PARAMS = frozenset({"options", "load_balance_hosts", "replication"})


def _stricter_than_asyncpg(key: str, value: str) -> bool:
    """A libpq setting that makes the connection stricter in a way asyncpg cannot apply: dropped,
    the analytics connections would be weaker than the URL asks for."""
    value = value.lower()
    return key in ("require_auth", "sslcrldir", "requirepeer") or (key, value) in {
        ("channel_binding", "require"),
        ("gssencmode", "require"),
        ("sslcertmode", "require"),
        ("requiressl", "1"),
    }


_UNREADABLE_URL = (
    "the analytics database URL cannot be read the same way by every client: percent-encode '@', ':', "
    "'/', '?', '#', '&', '=' and '%' in its user name and password, name one host, and remove control "
    "characters such as a trailing newline"
)
# Connection parts a URL gives before its "?" or as parameters: read once, from either place.
_URL_PARTS = frozenset({"user", "password", "host", "port", "dbname"})
_HOST_NAME = re.compile(r"[A-Za-z0-9._-]+")  # a DNS name or an IPv4 address
_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})|%00")  # libpq refuses both
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")  # e.g. a secret file's trailing newline, which urllib drops
_PORT = re.compile(r"\d{1,5}", re.ASCII)


def _plain(value: str) -> bool:
    return quote(value, safe="") == value


@dataclass(frozen=True)
class _Target:
    """What a connection URL asks for, read once: each client's URL is rebuilt from it."""

    user: str | None = None
    password: str | None = None
    host: str | None = None  # a network host, or a socket directory ("/...")
    port: int | None = None
    dbname: str | None = None
    libpq_params: tuple[tuple[str, str], ...] = ()  # (name, decoded value): the migrations'
    asyncpg_params: tuple[tuple[str, str], ...] = ()  # the pool's

    @property
    def network_host(self) -> str | None:
        """The host, when the network reaches it: None for a socket directory or the drivers' defaults."""
        return None if self.host is None or self.host.startswith("/") else self.host

    def _userinfo(self) -> str:
        if self.user is None and self.password is None:
            return ""
        userinfo = quote(self.user or "", safe="")
        if self.password is not None:
            userinfo += ":" + quote(self.password, safe="")
        return userinfo + "@"

    def _location_and_query(self) -> tuple[str, list[tuple[str, str]]]:
        location, query = "", []
        if host := self.network_host:
            location = (f"[{host}]" if ":" in host else host) + (f":{self.port}" if self.port else "")
        elif self.host:
            query.append(("host", self.host))
            if self.port:
                query.append(("port", str(self.port)))
        if self.dbname and _plain(self.dbname):
            location += f"/{self.dbname}"
        elif self.dbname:
            query.append(("dbname", self.dbname))
        return location, query

    def url(self, params: tuple[tuple[str, str], ...]) -> str:
        """The target as a URL, fully percent-encoded, every value where both drivers decode it:
        SQLAlchemy passes the path on undecoded, and asyncpg dials an authority's ":port" without
        a host as the TCP host "", before a socket directory given as a parameter."""
        userinfo = self._userinfo()
        location, query = self._location_and_query()
        query_text = "&".join(f"{key}={quote(value, safe='')}" for key, value in [*query, *params])
        return f"{_POSTGRESQL_SCHEME}{userinfo}{location}" + (f"?{query_text}" if query_text else "")


def _port(text: str) -> int | None:
    if not text:
        return None
    if not (_PORT.fullmatch(text) and 0 < int(text) < 65536):
        raise CliAnalyticsStorageConfigError(
            "the analytics database URL has a port that is not a number from 1 to 65535"
        )
    return int(text)


def _network_host(text: str) -> bool:
    """A name, an IPv4 address or an IPv6 one without a zone: what every client reads undecoded."""
    if ":" not in text:
        return bool(_HOST_NAME.fullmatch(text))
    try:
        ipaddress.IPv6Address(text)
    except ValueError:
        return False
    return "%" not in text


def _host(text: str) -> str | None:
    """One host, as every client reads it: several hosts would let each driver pick its own."""
    if "," in text or (text and not text.startswith("/") and not _network_host(text)):
        raise CliAnalyticsStorageConfigError(_UNREADABLE_URL)
    return text or None


def _read_location(url: str) -> tuple[dict[str, object], str]:
    """The user, password, host, port and database before the URL's "?", and its query."""
    url = _SCHEME.sub(_POSTGRESQL_SCHEME, url, count=1)
    rest = url.removeprefix(_POSTGRESQL_SCHEME)
    if not url.startswith(_POSTGRESQL_SCHEME) or "#" in url or _BAD_ESCAPE.search(url) or _CONTROL.search(url):
        raise CliAnalyticsStorageConfigError(_UNREADABLE_URL)
    location, _, query = rest.partition("?")
    authority, _, path = location.partition("/")
    # "@" ends the user name and password, once, before the host. Anywhere else it is a user name
    # or password holding a raw "@", "/" or "?": libpq, asyncpg and SQLAlchemy each split it their
    # own way, and its fragments would become a host, a port, a database or parameters.
    if "@" in path or "@" in query or authority.count("@") > 1:
        raise CliAnalyticsStorageConfigError(_UNREADABLE_URL)
    parts: dict[str, object] = {"dbname": unquote(path) or None}
    userinfo, at, hostspec = authority.rpartition("@")
    if at:
        user, _, password = userinfo.partition(":")
        parts.update(user=unquote(user) or None, password=unquote(password) or None)
    if hostspec.startswith("["):  # an IPv6 address
        host, bracket, port = hostspec[1:].partition("]")
        if not bracket or (port and not port.startswith(":")):
            raise CliAnalyticsStorageConfigError(_UNREADABLE_URL)
        port = port[1:]
    else:
        host, _, port = hostspec.partition(":")
    parts.update(host=_host(unquote(host)), port=_port(port))
    return parts, query


def _query_params(query: str) -> list[tuple[str, str]]:
    """The query's parameters, decoded: known ones named once, in lowercase, `ssl` as `sslmode`."""
    pieces = [piece.partition("=") for piece in query.split("&") if piece]
    has_sslmode = any(unquote(key) == "sslmode" for key, _, _ in pieces)
    params, seen = [], set()
    for raw_key, equals, raw_value in pieces:
        if not equals:  # e.g. a password's tail after a raw "&"
            raise CliAnalyticsStorageConfigError(_UNREADABLE_URL)
        key, value = unquote(raw_key), unquote(raw_value)
        known = key.strip().lower()
        if known in _LIBPQ_PARAMS | {"ssl"} and key != known:  # dropped, it could leave TLS at "prefer"
            raise CliAnalyticsStorageConfigError(
                f"the analytics database URL parameter names are exact: write {known!r} in lowercase, without spaces"
            )
        if key == "ssl":
            if has_sslmode:  # an explicit sslmode wins
                continue
            key, value = "sslmode", _SSL_TO_SSLMODE.get(value.lower(), value)
        if key in _LIBPQ_PARAMS and key in seen:  # unknown names are never repeated back
            raise CliAnalyticsStorageConfigError(f"the analytics database URL sets {key} twice")
        seen.add(key)
        params.append((key, value))
    return params


def _fold(parts: dict[str, object], key: str, value: str) -> None:
    """A connection part given as a parameter: libpq and asyncpg disagree on which of two wins."""
    if parts.get(key) is not None:
        raise CliAnalyticsStorageConfigError(
            f"the analytics database URL sets {key} both before '?' and as a parameter"
        )
    if key == "port":
        parts[key] = _port(value)
    elif key == "host":
        parts[key] = _host(value)
    else:
        parts[key] = value or None


class _Params:
    """A URL's other parameters, sorted by which of the two connections can apply them."""

    def __init__(self) -> None:
        self.libpq: list[tuple[str, str]] = []
        self.asyncpg: list[tuple[str, str]] = []
        self.left_out: set[str] = set()
        self.migrations_only: set[str] = set()
        self.unknown = 0

    def add(self, key: str, value: str) -> None:
        if key not in _LIBPQ_PARAMS:
            self.unknown += 1  # never named: a malformed URL's fragments would be
        elif key in _SESSION_PARAMS:
            self.left_out.add(key)
        elif key in _ASYNCPG_PARAMS:
            self.libpq.append((key, value))
            self.asyncpg.append((key, value))
        elif key in _TARGET_PARAMS:
            raise CliAnalyticsStorageConfigError(
                f"the analytics database URL sets {key}, which the analytics connections (asyncpg) cannot apply; "
                "put the host, port, database and user in the URL itself"
            )
        elif _stricter_than_asyncpg(key, value):
            raise CliAnalyticsStorageConfigError(
                f"the analytics database URL sets {key}, which the analytics connections (asyncpg) cannot apply; "
                "remove it, or connect without it through a dedicated CLI_ANALYTICS_PG_URL"
            )
        else:
            self.libpq.append((key, value))
            self.migrations_only.add(key)

    def warn(self) -> None:
        if self.left_out or self.unknown:
            described = sorted(self.left_out) + (
                [f"{self.unknown} parameter(s) not known to libpq"] if self.unknown else []
            )
            logger.warning(
                "cli_analytics: analytics database URL parameters left out of both analytics connections: %s",
                ", ".join(described),
            )
        if self.migrations_only:
            logger.warning(
                "cli_analytics: analytics database URL parameters the migrations only apply (asyncpg cannot): %s",
                ", ".join(sorted(self.migrations_only)),
            )


def _read_url(url: str) -> _Target:
    """The URL read once, strictly, as libpq reads it: anything two clients could read differently
    is refused. Refusals and warnings never quote the URL, only known parameter names."""
    parts, query = _read_location(url)
    params = _Params()
    for key, value in _query_params(query):
        if key in _URL_PARTS:
            _fold(parts, key, value)
        elif key.strip().lower() == "database":
            # asyncpg reads it as dbname and libpq refuses it. Dropped, both connections would open the
            # user's default database, and the migrations would create the analytics schema there.
            raise CliAnalyticsStorageConfigError(
                "the analytics database URL sets database, which libpq does not know: name the database in the "
                "URL's path, or as dbname"
            )
        else:
            params.add(key, value)
    params.warn()
    return _Target(**parts, libpq_params=tuple(params.libpq), asyncpg_params=tuple(params.asyncpg))  # type: ignore[arg-type]


@dataclass(frozen=True)
class AnalyticsPgSettings:
    # The DSNs may carry a password, so they never appear in repr() or logs.
    dsn: str = field(repr=False)  # the pool's (asyncpg)
    schema: str
    iam_auth: bool
    pool_size: int
    statement_timeout_ms: int
    work_mem: str
    ingest_acquire_timeout_s: float
    raw_retention_days: int
    rollup_retention_days: int
    dedup_retention_days: int
    rollup_refresh_seconds: int
    rollup_batch_size: int
    partition_premake_weeks: int
    maintenance_interval_minutes: int
    ingest_statement_timeout_ms: int = 10_000
    session_retention_days: int = 0  # 0: the session tables are not purged by age
    migration_dsn: str = field(default="", repr=False)  # libpq's, for the migrations; `dsn` when empty

    @classmethod
    def from_config(cls, cfg: Config) -> AnalyticsPgSettings:
        target = cls._resolve_target(cfg)
        # Without one, asyncpg tries four socket directories, then TCP localhost (or dials a port's
        # TCP host "", or cannot pair the port with them), while libpq tries its one socket directory.
        if not target.host:
            raise CliAnalyticsStorageConfigError(
                "the analytics database connection names no host, so asyncpg and libpq would each try their own: "
                "name the host (localhost, say) or the socket directory (?host=/var/run/postgresql)"
            )
        # As PostgresClient: with IAM configured, every connection authenticates with a fresh
        # token, unless a dedicated analytics URL carries a password (that database uses passwords).
        iam_auth = bool(cfg.PG_IAM_AUTH_PROVIDER) and (not cfg.CLI_ANALYTICS_PG_URL or target.password is None)
        # An AWS token is signed for one host, port and user, POSTGRES_*'s when the URL names none
        # (GCP and Azure tokens are not): the drivers would log in elsewhere, or as PGUSER or the OS user.
        if iam_auth and cfg.PG_IAM_AUTH_PROVIDER == "aws" and not (target.network_host and target.user):
            raise CliAnalyticsStorageConfigError(
                "AWS IAM authentication needs a network host and a user in the analytics database URL"
            )
        # Refused here, not by the Config validator: a bad value must answer 503, not stop the application.
        session_days = cfg.CLI_ANALYTICS_SESSION_RETENTION_DAYS
        if 0 < session_days < cfg.CLI_ANALYTICS_RAW_RETENTION_DAYS:
            raise CliAnalyticsStorageConfigError(
                "CLI_ANALYTICS_SESSION_RETENTION_DAYS must be 0 or at least CLI_ANALYTICS_RAW_RETENTION_DAYS "
                f"({cfg.CLI_ANALYTICS_RAW_RETENTION_DAYS}), got {session_days}"
            )
        return cls(
            dsn=target.url(target.asyncpg_params),
            migration_dsn=target.url(target.libpq_params),
            schema=cfg.CLI_ANALYTICS_PG_SCHEMA,
            iam_auth=iam_auth,
            pool_size=cfg.CLI_ANALYTICS_PG_POOL_SIZE,
            statement_timeout_ms=cfg.CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS,
            work_mem=cfg.CLI_ANALYTICS_PG_WORK_MEM,
            ingest_acquire_timeout_s=cfg.CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS / 1000,
            raw_retention_days=cfg.CLI_ANALYTICS_RAW_RETENTION_DAYS,
            rollup_retention_days=cfg.CLI_ANALYTICS_ROLLUP_RETENTION_DAYS,
            dedup_retention_days=cfg.CLI_ANALYTICS_DEDUP_RETENTION_DAYS,
            rollup_refresh_seconds=cfg.CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS,
            rollup_batch_size=cfg.CLI_ANALYTICS_ROLLUP_BATCH_SIZE,
            partition_premake_weeks=cfg.CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS,
            maintenance_interval_minutes=cfg.CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES,
            ingest_statement_timeout_ms=cfg.CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS,
            session_retention_days=session_days,
        )

    @staticmethod
    def _resolve_target(cfg: Config) -> _Target:
        """CLI_ANALYTICS_PG_URL, else the application database (PG_URL, else POSTGRES_*)."""
        if cfg.CLI_ANALYTICS_PG_URL:
            return _read_url(cfg.CLI_ANALYTICS_PG_URL)
        if cfg.PG_URL:
            return _read_url(cfg.PG_URL)
        if cfg.PG_IAM_AUTH_PROVIDER:  # the password is a short-lived token fetched for every connection
            params: tuple[tuple[str, str], ...] = (("sslmode", "require"),)
            password = None
        else:
            params, password = (), cfg.POSTGRES_PASSWORD
        return _Target(
            user=cfg.POSTGRES_USER,
            password=password,
            host=_host(cfg.POSTGRES_HOST),
            port=cfg.POSTGRES_PORT,
            dbname=cfg.POSTGRES_DB,
            libpq_params=params,
            asyncpg_params=params,
        )

    def lock_key(self, name: str) -> int:
        """A stable signed 64-bit advisory-lock key for `name`, scoped to the analytics schema."""
        digest = hashlib.blake2b(f"codemie_cli_analytics:{self.schema}:{name}".encode(), digest_size=8)
        return int.from_bytes(digest.digest(), "big", signed=True)

    @property
    def iam_endpoint(self) -> tuple[str | None, int, str | None]:
        """Host, port and user of the DSN: an AWS RDS token is signed for exactly these."""
        parts = urlsplit(self.dsn)
        user = unquote(parts.username) if parts.username else None
        return parts.hostname, parts.port or _DEFAULT_PORT, user

    @property
    def sqlalchemy_url(self) -> str:
        """The same database for synchronous SQLAlchemy (Alembic migrations)."""
        return _SCHEME.sub("postgresql+psycopg2://", self.migration_dsn or self.dsn, count=1)

    def server_settings(self) -> dict[str, str]:
        """Session settings of every analytics connection (RESET ALL returns to these)."""
        return {
            "search_path": self.schema,
            "application_name": APPLICATION_NAME,
            "work_mem": self.work_mem,
            "statement_timeout": str(self.statement_timeout_ms),
            # A plan over every weekly partition costs past jit_above_cost, so PostgreSQL
            # compiles hundreds of functions per query: measured, ~500 ms added to a 3 ms
            # session-detail query, and no endpoint faster with JIT.
            "jit": "off",
        }
