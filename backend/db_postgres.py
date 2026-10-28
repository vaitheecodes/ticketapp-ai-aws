"""PostgreSQL backend (DB_BACKEND=postgres), behind backend/db.py.

The rest of the backend talks DB-API with SQLite-style SQL; this module
makes that run on Postgres instead of rewriting ~200 queries:

- Placeholders: `?` -> `%s`, `:name` -> `%(name)s` (outside quoted text),
  and `%` -> `%%`.
- `rowid` (SQLite's ORDER BY tiebreak) -> `seq`, an identity column the
  schema adds to the tables ordered that way. `seq` is never returned.
- Rows behave like sqlite3.Row: by name, by position, dict(row).
- TIMESTAMPTZ comes back as an ISO-8601 UTC string (what the app stores
  and compares in SQLite); aggregate NUMERICs as int/float; bool
  parameters are sent as 0/1 (flags are SMALLINT 0/1).

Connections come from one psycopg_pool.ConnectionPool per process with
an explicit max size (DB_POOL_MAX) and wait timeout (DB_POOL_TIMEOUT);
close() rolls back anything uncommitted and returns the connection to
the pool. Settings come from DATABASE_URL or DB_HOST / DB_PORT / DB_NAME /
DB_USER / DB_PASSWORD / DB_SSLMODE. Neither the URL nor the password is
ever logged or put in an error message.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.datetime import TimestamptzLoader
from psycopg.types.numeric import NumericLoader
from psycopg_pool import ConnectionPool, PoolTimeout

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "postgres"
DEFAULT_POOL_MAX = 10
DEFAULT_POOL_TIMEOUT = 10.0
HIDDEN_COLUMNS = frozenset({"seq"})

_pool: ConnectionPool | None = None
_pool_key: tuple | None = None
_pool_lock = threading.Lock()


class DatabaseConfigError(RuntimeError):
    """Postgres settings are missing or unusable (message names the
    variable, never a value)."""


class DatabaseBusy(RuntimeError):
    """No pooled connection became free within DB_POOL_TIMEOUT."""


class DatabaseUnavailable(RuntimeError):
    """Postgres could not be reached (message has no credentials)."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def conninfo() -> str:
    url = _env("DATABASE_URL")
    if url:
        return url
    host, name, user = _env("DB_HOST"), _env("DB_NAME"), _env("DB_USER")
    missing = [n for n, v in (("DB_HOST", host), ("DB_NAME", name), ("DB_USER", user)) if not v]
    if missing:
        raise DatabaseConfigError(
            "DB_BACKEND=postgres needs DATABASE_URL or " + ", ".join(missing))
    return make_conninfo(host=host, port=_env("DB_PORT") or "5432", dbname=name, user=user,
                         password=_env("DB_PASSWORD") or "", sslmode=_env("DB_SSLMODE") or "prefer")


def describe() -> str:
    """host:port/dbname, safe to log (no user or password)."""
    try:
        parts = conninfo_to_dict(conninfo())
    except Exception:  # noqa: BLE001
        return "(unparseable settings)"
    return f"{parts.get('host', '?')}:{parts.get('port', '5432')}/{parts.get('dbname', '?')}"


def _number(name: str, default: float, cast=float):
    try:
        return cast(_env(name) or default)
    except ValueError:
        raise DatabaseConfigError(f"{name} must be a number") from None


def pool_settings() -> dict:
    max_size = _number("DB_POOL_MAX", DEFAULT_POOL_MAX, int)
    return {"min_size": min(_number("DB_POOL_MIN", 1, int), max_size), "max_size": max_size,
            "timeout": _number("DB_POOL_TIMEOUT", DEFAULT_POOL_TIMEOUT)}


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class _IsoTimestamptz(TimestamptzLoader):
    def load(self, data):
        return super().load(data).astimezone(timezone.utc).isoformat()


class _PlainNumeric(NumericLoader):
    """COUNT/SUM/AVG of integers come back as NUMERIC: int when whole."""

    def load(self, data):
        value = super().load(data)
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        return value


class Row:
    """sqlite3.Row lookalike: row["col"], row[0], len, iteration, dict(row)."""

    __slots__ = ("_values", "_index")

    def __init__(self, values, index: dict[str, int]):
        self._values = values
        self._index = index

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._values[self._index[key]]
            except KeyError:
                raise IndexError(f"No item with that key: {key}") from None
        return self._values[key]

    def keys(self) -> list[str]:
        return list(self._index)

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other):
        if isinstance(other, Row):
            return self._values == other._values and self._index == other._index
        return NotImplemented

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._index, self._values))!r})"


def _row_factory(cursor):
    if cursor.description is None:
        return None
    names = [c.name for c in cursor.description]
    keep = [i for i, n in enumerate(names) if n not in HIDDEN_COLUMNS]
    index = {names[i]: pos for pos, i in enumerate(keep)}
    if len(keep) == len(names):
        return lambda values: Row(values, index)
    return lambda values: Row(tuple(values[i] for i in keep), index)


# ---------------------------------------------------------------------------
# SQL translation
# ---------------------------------------------------------------------------
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ROWID = re.compile(r"\browid\b", re.IGNORECASE)


@lru_cache(maxsize=2048)
def translate(sql: str) -> str:
    """SQLite-style SQL -> psycopg. Quoted text ('...' and "...") is left
    alone apart from doubling %, which psycopg would otherwise read as a
    placeholder."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # '' escape
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1].replace("%", "%%"))
            i = j + 1
        elif ch == "?":
            out.append("%s")
            i += 1
        elif ch == "%":
            out.append("%%")
            i += 1
        elif ch == ":" and i + 1 < n and sql[i + 1] == ":":  # ::cast
            out.append("::")
            i += 2
        elif ch == ":" and (m := _NAME.match(sql, i + 1)):
            out.append(f"%({m.group(0)})s")
            i = m.end()
        else:
            j = i
            while j < n and sql[j] not in "'\"?%:":
                j += 1
            out.append(_ROWID.sub("seq", sql[i:j]))
            i = j
    return "".join(out)


def _adapt(value):
    return int(value) if isinstance(value, bool) else value


def _params(params):
    if params is None:
        return ()
    if isinstance(params, dict):
        return {k: _adapt(v) for k, v in params.items()}
    return tuple(_adapt(v) for v in params)


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
class Connection:
    """A pooled psycopg connection with sqlite3.Connection's surface."""

    backend = "postgres"

    def __init__(self, pool: ConnectionPool, raw: psycopg.Connection):
        self._pool = pool
        self._raw = raw

    @property
    def raw(self) -> psycopg.Connection:
        if self._raw is None:
            raise psycopg.InterfaceError("connection already returned to the pool")
        return self._raw

    # sqlite3.Connection compatibility; rows are always Row-like.
    row_factory = None

    def execute(self, sql: str, params=()):
        cursor = self.raw.cursor()
        cursor.execute(translate(sql), _params(params))
        return cursor

    def executemany(self, sql: str, seq_of_params):
        cursor = self.raw.cursor()
        cursor.executemany(translate(sql), [_params(p) for p in seq_of_params])
        return cursor

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    @property
    def in_transaction(self) -> bool:
        return self.raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def close(self) -> None:
        """Return to the pool; anything uncommitted is rolled back."""
        raw, self._raw = self._raw, None
        if raw is None:
            return
        try:
            if not raw.closed and raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                raw.rollback()
        except psycopg.Error:
            pass  # the pool discards broken connections
        self._pool.putconn(raw)

    # `with conn:` commits on success, rolls back on error (sqlite3 semantics).
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def _configure(raw: psycopg.Connection) -> None:
    raw.adapters.register_loader("timestamptz", _IsoTimestamptz)
    raw.adapters.register_loader("numeric", _PlainNumeric)
    raw.row_factory = _row_factory
    raw.execute("SET TIME ZONE 'UTC'")
    raw.commit()


def get_pool() -> ConnectionPool:
    global _pool, _pool_key
    info, settings = conninfo(), pool_settings()
    key = (info, tuple(sorted(settings.items())))
    with _pool_lock:
        if _pool is None or _pool_key != key:
            if _pool is not None:
                _pool.close()
            _pool = ConnectionPool(
                info, min_size=settings["min_size"], max_size=settings["max_size"],
                timeout=settings["timeout"], configure=_configure, check=ConnectionPool.check_connection,
                kwargs={"autocommit": False}, name="ticketapp", open=True,
            )
            _pool_key = key
            logger.info("Postgres pool for %s (max %d, wait %.0fs)", describe(),
                        settings["max_size"], settings["timeout"])
        return _pool


def close_pool() -> None:
    global _pool, _pool_key
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool, _pool_key = None, None


def get_connection() -> Connection:
    pool = get_pool()
    try:
        return Connection(pool, pool.getconn())
    except PoolTimeout:
        raise DatabaseBusy(f"No database connection free within {pool.timeout:.0f}s "
                           f"(pool max {pool.max_size})") from None


def wait_until_ready(timeout: float = 30.0) -> None:
    """Fail fast at startup with a clear, credential-free message."""
    try:
        get_pool().wait(timeout=timeout)
    except PoolTimeout:
        raise DatabaseUnavailable(f"Could not connect to Postgres at {describe()} "
                                  f"within {timeout:.0f}s") from None


def begin_write(conn: Connection, lock_key: str | None = None) -> None:
    """Postgres transactions start implicitly; a check-then-write that
    must not race another request takes a transaction-scoped advisory
    lock on `lock_key` (released at commit/rollback)."""
    if lock_key:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (lock_key,))


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def migrate() -> list[str]:
    """Apply pending migrations in order, each in its own transaction,
    under an advisory lock so concurrent app starts don't race. Returns
    the versions applied."""
    applied = []
    conn = get_connection()
    try:
        raw = conn.raw
        raw.execute("SELECT pg_advisory_lock(hashtextextended('ticketapp-migrations', 0))")
        try:
            raw.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                        "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            raw.commit()
            done = {r[0] for r in raw.execute("SELECT version FROM schema_migrations")}
            raw.commit()
            for path in migration_files():
                version = path.stem
                if version in done:
                    continue
                with raw.transaction():
                    raw.execute(path.read_text())
                    raw.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied.append(version)
                logger.info("Applied migration %s", version)
        finally:
            raw.execute("SELECT pg_advisory_unlock(hashtextextended('ticketapp-migrations', 0))")
            raw.commit()
    finally:
        conn.close()
    return applied


def table_names(conn: Connection) -> list[str]:
    return [r["table_name"] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'")]
