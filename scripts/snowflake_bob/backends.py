"""Where a predefined query runs: the demo database (no account) or Snowflake (the official connector).

Which one: SNOWFLAKE_BACKEND=demo|connector; unset means connector once SNOWFLAKE_ACCOUNT is set, demo otherwise. A
report built on the demo source says so on the page and in the tool's answer, so made-up numbers cannot pass for real.

The connector signs in with SSO in a browser (SNOWFLAKE_AUTHENTICATOR=externalbrowser, the default) or a key pair
(SNOWFLAKE_PRIVATE_KEY_FILE). There is no password setting on purpose. It is an optional install:
`pip install -r requirements-snowflake.txt`. Give it a role that can only read.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from snowflake_bob import queries

MAX_ROWS = 50_000
STATEMENT_TIMEOUT_S = 120
_NAMED = re.compile(r":([a-z_]+)\b")
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def settings() -> dict:
    """The repo's .env under the process environment: a tool process does not inherit .env, and a rotated value applies
    from the next report."""
    from dotenv import dotenv_values  # noqa: PLC0415

    return {**{k: v for k, v in dotenv_values(ENV_FILE).items() if v}, **os.environ}


class BackendError(RuntimeError):
    """The query could not run: not configured, the connector missing, or the warehouse refused it."""


def _plain(v):
    """Warehouse values as the reports expect them: ISO text for dates, floats for numbers."""
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _cell(name: str, v):
    """A day column held as a midnight TIMESTAMP is still a day: reports compare these as YYYY-MM-DD."""
    if name.endswith("_DAY") and isinstance(v, datetime) and not (v.hour or v.minute or v.second):
        return v.date().isoformat()
    return _plain(v)


def _rows(raw) -> list[dict]:
    return [{str(k).upper(): _cell(str(k).upper(), v) for k, v in dict(r).items()} for r in raw]


class DemoBackend:
    """The generated data set in an in-memory SQLite database. It runs the same text a warehouse runs."""

    is_demo = True
    prefix = ""

    def __init__(self, connection=None):
        self._con = connection

    def run(self, query_id: str, params: dict) -> list[dict]:
        clean = queries.validate(query_id, params)
        if self._con is None:
            from snowflake_bob import demo_data  # noqa: PLC0415 -- only the demo source pays for the generator
            self._con = demo_data.database()
        cur = self._con.execute(queries.statement(query_id, self.prefix), clean)
        names = [c[0] for c in cur.description]
        return _rows(dict(zip(names, r, strict=True)) for r in cur.fetchall())

    def close(self) -> None:
        """The in-memory database lives as long as the source; nothing to release per report."""


@dataclass(frozen=True)
class ConnectorConfig:
    account: str
    user: str
    role: str = ""
    warehouse: str = ""
    authenticator: str = "externalbrowser"
    private_key_file: str = ""
    schema: str = queries.DEFAULT_SCHEMA

    @classmethod
    def from_env(cls, env=None) -> ConnectorConfig:
        env = settings() if env is None else env
        account, user = env.get("SNOWFLAKE_ACCOUNT", "").strip(), env.get("SNOWFLAKE_USER", "").strip()
        if not account or not user:
            raise BackendError("not configured: set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER in .env")
        return cls(account=account, user=user, role=env.get("SNOWFLAKE_ROLE", "").strip(),
                   warehouse=env.get("SNOWFLAKE_WAREHOUSE", "").strip(),
                   authenticator=env.get("SNOWFLAKE_AUTHENTICATOR", "").strip() or "externalbrowser",
                   private_key_file=env.get("SNOWFLAKE_PRIVATE_KEY_FILE", "").strip(),
                   schema=env.get("SNOWFLAKE_SCHEMA", "").strip() or queries.DEFAULT_SCHEMA)

    def connect_args(self) -> dict:
        # login_timeout: the SSO browser wait alone can take 120 s
        args = {"account": self.account, "user": self.user, "login_timeout": 180,
                "network_timeout": STATEMENT_TIMEOUT_S + 30,
                "session_parameters": {"STATEMENT_TIMEOUT_IN_SECONDS": STATEMENT_TIMEOUT_S,
                                       "QUERY_TAG": "bob-report"}}
        if self.role:
            args["role"] = self.role
        if self.warehouse:
            args["warehouse"] = self.warehouse
        if self.private_key_file:
            args["private_key_file"] = self.private_key_file
        else:
            args["authenticator"] = self.authenticator
            # a tool process can be short-lived: without the cached token every report would open a sign-in tab.
            # The cache needs the connector's secure-local-storage extra (requirements-snowflake.txt).
            args["client_store_temporary_credential"] = True
        return args


def connector_sql(text: str) -> str:
    """`:name` placeholders in the connector's own client-side style; a literal % is doubled."""
    return _NAMED.sub(lambda m: f"%({m.group(1)})s", text.replace("%", "%%"))


class ConnectorBackend:
    """One connection per report. Errors name the failing step and never carry the statement's values."""

    is_demo = False

    def __init__(self, config: ConnectorConfig, connector=None):
        self.config = config
        self.prefix = queries.schema_prefix(config.schema)
        self._connector = connector
        self._con = None

    def _module(self):
        if self._connector is None:
            try:
                import snowflake.connector as connector  # noqa: PLC0415 -- optional install
            except ImportError:
                raise BackendError("the Snowflake connector is not installed: "
                                   "pip install -r requirements-snowflake.txt") from None
            self._connector = connector
        return self._connector

    def _connection(self):
        if self._con is None:
            module = self._module()
            try:
                # SSO sign-in prints its prompts to stdout, which is the tool's channel to the model in stdio mode
                with contextlib.redirect_stdout(sys.stderr):
                    self._con = module.connect(**self.config.connect_args())
            except Exception as exc:  # noqa: BLE001 -- the driver's error classes are not importable without it
                raise BackendError(f"sign-in failed: {type(exc).__name__}") from None
        return self._con

    def run(self, query_id: str, params: dict) -> list[dict]:
        clean = queries.validate(query_id, params)
        con = self._connection()
        cur = con.cursor(self._module().DictCursor)
        try:
            cur.execute(connector_sql(queries.statement(query_id, self.prefix)), clean)
            rows = cur.fetchmany(MAX_ROWS + 1)
            if len(rows) > MAX_ROWS:      # a page drawn from part of a result would show wrong totals
                raise BackendError(f"{query_id} returned more than {MAX_ROWS} rows: narrow the report")
            return _rows(rows)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001 -- as above
            raise BackendError(f"{query_id} failed: {type(exc).__name__}") from None
        finally:
            cur.close()

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


def from_env(env=None):
    """The configured source."""
    env = settings() if env is None else env
    choice = env.get("SNOWFLAKE_BACKEND", "").strip().lower()
    if not choice:
        choice = "connector" if env.get("SNOWFLAKE_ACCOUNT", "").strip() else "demo"
    if choice == "demo":
        return DemoBackend()
    if choice == "connector":
        return ConnectorBackend(ConnectorConfig.from_env(env))
    raise BackendError("SNOWFLAKE_BACKEND must be demo or connector")
