"""Thin wrapper around the official Neo4j driver.

Everything above this module works with lists of plain dicts, so nothing else
has to import ``neo4j`` or know what a ``Record`` is. That keeps the analysis
code testable and means the whole database dependency is one small file.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from types import TracebackType
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.exceptions import (
    AuthError,
    DriverError,
    Neo4jError,
    ServiceUnavailable,
    SessionExpired,
)

from faultline.config import Settings, get_settings
from faultline.logging import get_logger

logger = get_logger(__name__)

Params = dict[str, Any]
Rows = list[dict[str, Any]]


class GraphUnavailable(RuntimeError):
    """Neo4j could not be reached. The API turns this into a 503."""


def _run(tx: ManagedTransaction, query: str, params: Params) -> Rows:
    return [record.data() for record in tx.run(query, params)]


class GraphClient:
    """Owns the driver and the read/write helpers built on top of it."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._driver: Driver | None = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            try:
                self._driver = GraphDatabase.driver(
                    self.settings.neo4j_uri,
                    auth=(self.settings.neo4j_user, self.settings.neo4j_password),
                    max_connection_pool_size=self.settings.neo4j_max_connection_pool_size,
                )
            except (ServiceUnavailable, AuthError, ValueError) as exc:
                raise GraphUnavailable(str(exc)) from exc
        return self._driver

    def verify(self) -> bool:
        """True when the database answers. Never raises — used by ``/health``."""
        try:
            self.driver.verify_connectivity()
        except (GraphUnavailable, DriverError, Neo4jError, OSError) as exc:
            logger.warning("neo4j unreachable", extra={"error": str(exc)})
            return False
        return True

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- queries -------------------------------------------------------------

    def read(self, query: str, /, **params: Any) -> Rows:
        """Run a read query in a managed transaction (the driver retries)."""
        try:
            with self.driver.session(database=self.settings.neo4j_database) as session:
                return session.execute_read(_run, query, params)
        except (ServiceUnavailable, SessionExpired, AuthError) as exc:
            raise GraphUnavailable(str(exc)) from exc

    def write(self, query: str, /, **params: Any) -> Rows:
        try:
            with self.driver.session(database=self.settings.neo4j_database) as session:
                return session.execute_write(_run, query, params)
        except (ServiceUnavailable, SessionExpired, AuthError) as exc:
            raise GraphUnavailable(str(exc)) from exc

    def run_autocommit(self, query: str, /, **params: Any) -> Rows:
        """For statements that must not sit inside an explicit transaction,
        such as ``CALL { ... } IN TRANSACTIONS``."""
        try:
            with self.driver.session(database=self.settings.neo4j_database) as session:
                return [record.data() for record in session.run(query, params)]
        except (ServiceUnavailable, SessionExpired, AuthError) as exc:
            raise GraphUnavailable(str(exc)) from exc

    def write_batches(
        self, query: str, rows: Sequence[dict[str, Any]], *, batch_size: int | None = None
    ) -> int:
        """Send ``rows`` through ``query`` in ``UNWIND``-sized chunks.

        One round trip per thousand rows instead of one per row is the whole
        difference between ingesting a large repository in seconds and in
        minutes. Each batch is its own managed transaction, so a transient
        failure retries that batch rather than the entire repository.
        """
        if not rows:
            return 0
        size = batch_size or self.settings.write_batch_size
        written = 0
        try:
            with self.driver.session(database=self.settings.neo4j_database) as session:
                for chunk in _chunks(rows, size):
                    session.execute_write(_run, query, {"rows": chunk})
                    written += len(chunk)
        except (ServiceUnavailable, SessionExpired, AuthError) as exc:
            raise GraphUnavailable(str(exc)) from exc
        return written


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


def coerce_rows(rows: Iterable[dict[str, Any]]) -> Rows:
    """Materialise a lazily produced row iterable."""
    return list(rows)
