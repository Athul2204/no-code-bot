"""Abstract database adapter. New engines (MySQL, PostgreSQL) implement this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DatabaseError(Exception):
    """A user-presentable database problem (never includes credentials)."""


class DatabaseAdapter(ABC):
    db_type: str = ""

    @abstractmethod
    def test(self) -> dict: ...

    @abstractmethod
    def list_tables(self) -> list[str]: ...

    @abstractmethod
    def describe(self, table: str) -> list[dict]:
        """Return [{name, type, notnull, pk, default}] for the table."""

    @abstractmethod
    def select(self, table: str, columns: list[str] | None, filters: list[dict], limit: int) -> list[dict]: ...

    @abstractmethod
    def get_by_id(self, table: str, id_column: str, value: Any, columns: list[str] | None) -> dict | None: ...

    @abstractmethod
    def insert(self, table: str, values: dict[str, Any], dry_run: bool = False) -> dict: ...

    @abstractmethod
    def update(self, table: str, id_column: str, id_value: Any, values: dict[str, Any], dry_run: bool = False) -> dict: ...
