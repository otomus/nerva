"""SQLite-backed registry — persistent single-node component catalog.

Implements the ``Registry`` protocol using stdlib ``sqlite3`` for persistence
across process restarts. Complex fields (schema, metadata, stats, requirements,
permissions) are stored as JSON text columns.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from nerva.registry import (
    ComponentKind,
    HealthStatus,
    InvocationStats,
    RegistryEntry,
    RegistryPatch,
)

if TYPE_CHECKING:
    from nerva.context import ExecContext


TABLE_NAME = "components"
"""Name of the SQLite table used for component storage."""

# Fields on RegistryEntry that RegistryPatch can overwrite.
_PATCHABLE_FIELDS = (
    "description",
    "metadata",
    "health",
    "enabled",
    "requirements",
    "permissions",
)

_CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        name            TEXT PRIMARY KEY,
        kind            TEXT NOT NULL,
        description     TEXT NOT NULL,
        schema_json     TEXT,
        metadata_json   TEXT NOT NULL DEFAULT '{{}}',
        health          TEXT NOT NULL DEFAULT 'healthy',
        stats_json      TEXT NOT NULL DEFAULT '{{}}',
        enabled         INTEGER NOT NULL DEFAULT 1,
        requirements_json TEXT NOT NULL DEFAULT '[]',
        permissions_json  TEXT NOT NULL DEFAULT '[]',
        updated_at      REAL NOT NULL
    )
"""


class SqliteRegistry:
    """Registry backed by SQLite for persistence across restarts.

    Creates a single table ``components`` with columns matching
    ``RegistryEntry`` fields. Complex fields are serialized as JSON text.
    Thread-safe via sqlite3's default serialized threading mode.

    Args:
        path: Path to SQLite database file. Use ``":memory:"`` for testing.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    async def register(self, entry: RegistryEntry, ctx: ExecContext) -> None:
        """Add or replace a component in the registry.

        Uses ``INSERT OR REPLACE`` so existing entries are overwritten.

        Args:
            entry: Component definition to register.
            ctx: Execution context carrying identity and permissions.
        """
        row = _entry_to_row(entry)
        self._conn.execute(
            f"""INSERT OR REPLACE INTO {TABLE_NAME}
                (name, kind, description, schema_json, metadata_json,
                 health, stats_json, enabled, requirements_json,
                 permissions_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        self._conn.commit()

    async def discover(
        self, kind: ComponentKind, ctx: ExecContext,
    ) -> list[RegistryEntry]:
        """List components of a given kind visible to the caller.

        Filters out disabled entries, unavailable entries, and entries
        whose required permissions are not satisfied by ``ctx.permissions``.

        Args:
            kind: Component type to filter by.
            ctx: Execution context used for permission checks.

        Returns:
            List of matching ``RegistryEntry`` objects, sorted by name.
        """
        rows = self._conn.execute(
            f"""SELECT * FROM {TABLE_NAME}
                WHERE kind = ? AND enabled = 1 AND health != ?
                ORDER BY name""",
            (kind.value, HealthStatus.UNAVAILABLE.value),
        ).fetchall()

        return _filter_by_permissions(rows, ctx)

    async def resolve(
        self, name: str, ctx: ExecContext,
    ) -> RegistryEntry | None:
        """Look up a single component by name.

        Args:
            name: Unique component identifier.
            ctx: Execution context (reserved for future permission gating).

        Returns:
            The matching ``RegistryEntry``, or ``None`` if not found.
        """
        row = self._conn.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE name = ?", (name,),
        ).fetchone()

        if row is None:
            return None
        return _row_to_entry(row)

    async def health(self, name: str) -> HealthStatus:
        """Get the current health status of a component.

        Args:
            name: Unique component identifier.

        Returns:
            Current ``HealthStatus``.

        Raises:
            KeyError: If no component with the given name is registered.
        """
        row = self._conn.execute(
            f"SELECT health FROM {TABLE_NAME} WHERE name = ?", (name,),
        ).fetchone()

        if row is None:
            raise KeyError(f"Component not found: {name!r}")
        return HealthStatus(row["health"])

    async def update(self, name: str, patch: RegistryPatch) -> None:
        """Apply a partial update to a registered component.

        Only non-``None`` fields in the patch are written. The entry
        is read, patched in memory, then written back as a full row.

        Args:
            name: Unique component identifier to update.
            patch: Fields to overwrite.

        Raises:
            KeyError: If no component with the given name is registered.
        """
        entry = await self.resolve(name, _NOOP_CTX)
        if entry is None:
            raise KeyError(f"Component not found: {name!r}")

        _apply_patch(entry, patch)
        row = _entry_to_row(entry)
        self._conn.execute(
            f"""INSERT OR REPLACE INTO {TABLE_NAME}
                (name, kind, description, schema_json, metadata_json,
                 health, stats_json, enabled, requirements_json,
                 permissions_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _entry_to_row(entry: RegistryEntry) -> tuple[
    str, str, str, str | None, str, str, str, int, str, str, float,
]:
    """Serialize a RegistryEntry into a SQLite row tuple.

    Args:
        entry: Entry to serialize.

    Returns:
        Tuple of column values matching the table schema.
    """
    return (
        entry.name,
        entry.kind.value,
        entry.description,
        json.dumps(entry.schema) if entry.schema is not None else None,
        json.dumps(entry.metadata),
        entry.health.value,
        _stats_to_json(entry.stats),
        1 if entry.enabled else 0,
        json.dumps(entry.requirements),
        json.dumps(entry.permissions),
        time.time(),
    )


def _row_to_entry(row: sqlite3.Row) -> RegistryEntry:
    """Deserialize a SQLite row into a RegistryEntry.

    Args:
        row: Database row with named columns.

    Returns:
        Fully populated ``RegistryEntry``.
    """
    return RegistryEntry(
        name=row["name"],
        kind=ComponentKind(row["kind"]),
        description=row["description"],
        schema=json.loads(row["schema_json"]) if row["schema_json"] else None,
        metadata=json.loads(row["metadata_json"]),
        health=HealthStatus(row["health"]),
        stats=_stats_from_json(row["stats_json"]),
        enabled=bool(row["enabled"]),
        requirements=json.loads(row["requirements_json"]),
        permissions=json.loads(row["permissions_json"]),
    )


def _stats_to_json(stats: InvocationStats) -> str:
    """Serialize InvocationStats to a JSON string.

    Args:
        stats: Stats dataclass to serialize.

    Returns:
        JSON string representation.
    """
    return json.dumps({
        "total_calls": stats.total_calls,
        "successes": stats.successes,
        "failures": stats.failures,
        "last_invoked_at": stats.last_invoked_at,
        "avg_duration_ms": stats.avg_duration_ms,
    })


def _stats_from_json(raw: str) -> InvocationStats:
    """Deserialize a JSON string into InvocationStats.

    Args:
        raw: JSON string (may be empty ``"{}"``).

    Returns:
        Populated ``InvocationStats`` with defaults for missing fields.
    """
    data: dict[str, object] = json.loads(raw) if raw else {}
    return InvocationStats(
        total_calls=int(data.get("total_calls", 0)),
        successes=int(data.get("successes", 0)),
        failures=int(data.get("failures", 0)),
        last_invoked_at=_to_optional_float(data.get("last_invoked_at")),
        avg_duration_ms=float(data.get("avg_duration_ms", 0.0)),
    )


def _to_optional_float(value: object) -> float | None:
    """Convert a value to float or None.

    Args:
        value: Raw value from JSON (int, float, or None).

    Returns:
        Float value or None.
    """
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Permission filtering
# ---------------------------------------------------------------------------


def _filter_by_permissions(
    rows: list[sqlite3.Row], ctx: ExecContext,
) -> list[RegistryEntry]:
    """Convert rows to entries, filtering out those the caller cannot access.

    An entry with no declared permissions is visible to everyone.
    An entry with permissions requires the caller to hold at least one
    matching role in ``ctx.permissions.roles``.

    Args:
        rows: Raw database rows.
        ctx: Execution context with caller permissions.

    Returns:
        List of accessible ``RegistryEntry`` objects.
    """
    results: list[RegistryEntry] = []
    caller_roles = ctx.permissions.roles

    for row in rows:
        entry = _row_to_entry(row)
        if entry.permissions and not _caller_has_permission(entry, caller_roles):
            continue
        results.append(entry)

    return results


def _caller_has_permission(
    entry: RegistryEntry, caller_roles: frozenset[str],
) -> bool:
    """Check that the caller holds at least one required role.

    Args:
        entry: Entry with a non-empty permissions list.
        caller_roles: Roles held by the caller.

    Returns:
        ``True`` if the caller has at least one matching role.
    """
    return bool(caller_roles & frozenset(entry.permissions))


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


def _apply_patch(entry: RegistryEntry, patch: RegistryPatch) -> None:
    """Write non-None patch fields onto the entry.

    Args:
        entry: Entry to mutate in place.
        patch: Partial update — only non-None fields are applied.
    """
    for field_name in _PATCHABLE_FIELDS:
        value = getattr(patch, field_name)
        if value is not None:
            setattr(entry, field_name, value)


# ---------------------------------------------------------------------------
# Internal no-op context for update() resolve call
# ---------------------------------------------------------------------------


class _NoopCtx:
    """Minimal stand-in for ExecContext used internally by update().

    Only ``update()`` calls ``resolve()`` internally — it does not need
    a real ExecContext since resolve performs no permission checks.
    """

    request_id: str = ""
    session_id: str | None = None
    permissions: object = None


_NOOP_CTX: ExecContext = _NoopCtx()  # type: ignore[assignment]
