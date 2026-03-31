"""Tests for SqliteRegistry — N-183.

Covers persistence, filtered discovery, health transitions, stats recording,
resolve, and edge cases like duplicates, SQL injection attempts, very long
names, and concurrent access.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

from nerva.context import Permissions
from nerva.registry import (
    ComponentKind,
    HealthStatus,
    InvocationStats,
    RegistryEntry,
    RegistryPatch,
)
from nerva.registry.sqlite import SqliteRegistry

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    name: str = "handler_a",
    kind: ComponentKind = ComponentKind.AGENT,
    description: str = "A test handler",
    enabled: bool = True,
    health: HealthStatus = HealthStatus.HEALTHY,
    permissions: list[str] | None = None,
    metadata: dict[str, str] | None = None,
    requirements: list[str] | None = None,
) -> RegistryEntry:
    """Build a RegistryEntry with sensible defaults.

    Args:
        name: Component name.
        kind: Component type.
        description: Human-readable description.
        enabled: Whether the component is active.
        health: Operational health status.
        permissions: Role names required to access.
        metadata: Custom key-value fields.
        requirements: Dependency names.

    Returns:
        A RegistryEntry with the given configuration.
    """
    return RegistryEntry(
        name=name,
        kind=kind,
        description=description,
        enabled=enabled,
        health=health,
        permissions=permissions or [],
        metadata=metadata or {},
        requirements=requirements or [],
    )


# ---------------------------------------------------------------------------
# Cross-cutting: every test gets a fresh in-memory registry
# ---------------------------------------------------------------------------

@pytest.fixture
def registry() -> SqliteRegistry:
    """Provide a fresh in-memory SqliteRegistry for each test.

    Returns:
        A new SqliteRegistry backed by :memory:.
    """
    reg = SqliteRegistry(":memory:")
    yield reg
    reg.close()


# ---------------------------------------------------------------------------
# Registration and resolve
# ---------------------------------------------------------------------------

class TestRegisterAndResolve:
    """Tests for registering and resolving components."""

    @pytest.mark.asyncio
    async def test_register_and_resolve(self, registry: SqliteRegistry) -> None:
        """A registered entry can be resolved by name."""
        ctx = make_ctx()
        entry = _make_entry(name="search")
        await registry.register(entry, ctx)

        resolved = await registry.resolve("search", ctx)
        assert resolved is not None
        assert resolved.name == "search"
        assert resolved.kind == ComponentKind.AGENT

    @pytest.mark.asyncio
    async def test_resolve_missing_returns_none(self, registry: SqliteRegistry) -> None:
        """Resolving a non-existent name returns None."""
        ctx = make_ctx()
        assert await registry.resolve("nonexistent", ctx) is None

    @pytest.mark.asyncio
    async def test_register_duplicate_overwrites(self, registry: SqliteRegistry) -> None:
        """Re-registering the same name overwrites the existing entry."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="x", description="v1"), ctx)
        await registry.register(_make_entry(name="x", description="v2"), ctx)

        resolved = await registry.resolve("x", ctx)
        assert resolved.description == "v2"

    @pytest.mark.asyncio
    async def test_register_preserves_metadata(self, registry: SqliteRegistry) -> None:
        """Custom metadata survives serialization round-trip."""
        ctx = make_ctx()
        entry = _make_entry(name="meta", metadata={"role": "planner", "version": "1.0"})
        await registry.register(entry, ctx)

        resolved = await registry.resolve("meta", ctx)
        assert resolved.metadata["role"] == "planner"
        assert resolved.metadata["version"] == "1.0"

    @pytest.mark.asyncio
    async def test_register_preserves_schema(self, registry: SqliteRegistry) -> None:
        """JSON schema survives serialization."""
        ctx = make_ctx()
        entry = _make_entry(name="typed")
        entry.schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        await registry.register(entry, ctx)

        resolved = await registry.resolve("typed", ctx)
        assert resolved.schema is not None
        assert resolved.schema["type"] == "object"

    @pytest.mark.asyncio
    async def test_register_none_schema(self, registry: SqliteRegistry) -> None:
        """None schema round-trips correctly."""
        ctx = make_ctx()
        entry = _make_entry(name="no_schema")
        entry.schema = None
        await registry.register(entry, ctx)

        resolved = await registry.resolve("no_schema", ctx)
        assert resolved.schema is None


# ---------------------------------------------------------------------------
# Discovery with filtering
# ---------------------------------------------------------------------------

class TestDiscover:
    """Tests for filtered discovery by kind, health, and permissions."""

    @pytest.mark.asyncio
    async def test_discover_by_kind(self, registry: SqliteRegistry) -> None:
        """discover() returns only entries matching the requested kind."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="agent1", kind=ComponentKind.AGENT), ctx)
        await registry.register(_make_entry(name="tool1", kind=ComponentKind.TOOL), ctx)

        agents = await registry.discover(ComponentKind.AGENT, ctx)
        names = [e.name for e in agents]
        assert "agent1" in names
        assert "tool1" not in names

    @pytest.mark.asyncio
    async def test_discover_excludes_disabled(self, registry: SqliteRegistry) -> None:
        """Disabled entries are excluded from discovery."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="active", enabled=True), ctx)
        await registry.register(_make_entry(name="inactive", enabled=False), ctx)

        results = await registry.discover(ComponentKind.AGENT, ctx)
        names = [e.name for e in results]
        assert "active" in names
        assert "inactive" not in names

    @pytest.mark.asyncio
    async def test_discover_excludes_unavailable(self, registry: SqliteRegistry) -> None:
        """Unavailable entries are excluded from discovery."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="up", health=HealthStatus.HEALTHY), ctx)
        await registry.register(_make_entry(name="down", health=HealthStatus.UNAVAILABLE), ctx)

        results = await registry.discover(ComponentKind.AGENT, ctx)
        names = [e.name for e in results]
        assert "up" in names
        assert "down" not in names

    @pytest.mark.asyncio
    async def test_discover_includes_degraded(self, registry: SqliteRegistry) -> None:
        """Degraded entries are still included in discovery."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="slow", health=HealthStatus.DEGRADED), ctx)

        results = await registry.discover(ComponentKind.AGENT, ctx)
        names = [e.name for e in results]
        assert "slow" in names

    @pytest.mark.asyncio
    async def test_discover_no_matches(self, registry: SqliteRegistry) -> None:
        """discover() returns empty list when no entries match."""
        ctx = make_ctx()
        results = await registry.discover(ComponentKind.PLUGIN, ctx)
        assert results == []

    @pytest.mark.asyncio
    async def test_discover_permission_filtering(self, registry: SqliteRegistry) -> None:
        """Entries with permission requirements are filtered by caller roles."""
        ctx_admin = make_ctx(roles=frozenset(["admin"]))
        ctx_user = make_ctx(roles=frozenset(["user"]))
        ctx_anon = make_ctx(roles=frozenset())

        await registry.register(
            _make_entry(name="admin_tool", permissions=["admin"]),
            ctx_admin,
        )
        await registry.register(
            _make_entry(name="public_tool", permissions=[]),
            ctx_admin,
        )

        admin_results = await registry.discover(ComponentKind.AGENT, ctx_admin)
        user_results = await registry.discover(ComponentKind.AGENT, ctx_user)
        anon_results = await registry.discover(ComponentKind.AGENT, ctx_anon)

        admin_names = [e.name for e in admin_results]
        user_names = [e.name for e in user_results]
        anon_names = [e.name for e in anon_results]

        assert "admin_tool" in admin_names
        assert "public_tool" in admin_names
        assert "admin_tool" not in user_names
        assert "public_tool" in user_names
        assert "admin_tool" not in anon_names

    @pytest.mark.asyncio
    async def test_discover_sorted_by_name(self, registry: SqliteRegistry) -> None:
        """discover() returns entries sorted alphabetically by name."""
        ctx = make_ctx()
        for name in ["charlie", "alpha", "bravo"]:
            await registry.register(_make_entry(name=name), ctx)

        results = await registry.discover(ComponentKind.AGENT, ctx)
        names = [e.name for e in results]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    """Tests for health status queries and transitions."""

    @pytest.mark.asyncio
    async def test_health_default_is_healthy(self, registry: SqliteRegistry) -> None:
        """New entries default to HEALTHY."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="h"), ctx)
        assert await registry.health("h") == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_health_missing_raises(self, registry: SqliteRegistry) -> None:
        """health() raises KeyError for non-existent components."""
        with pytest.raises(KeyError, match="not found"):
            await registry.health("ghost")

    @pytest.mark.asyncio
    async def test_health_transitions(self, registry: SqliteRegistry) -> None:
        """Health can transition healthy -> degraded -> unavailable via update."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="svc"), ctx)

        await registry.update("svc", RegistryPatch(health=HealthStatus.DEGRADED))
        assert await registry.health("svc") == HealthStatus.DEGRADED

        await registry.update("svc", RegistryPatch(health=HealthStatus.UNAVAILABLE))
        assert await registry.health("svc") == HealthStatus.UNAVAILABLE

        await registry.update("svc", RegistryPatch(health=HealthStatus.HEALTHY))
        assert await registry.health("svc") == HealthStatus.HEALTHY


# ---------------------------------------------------------------------------
# Update / patch
# ---------------------------------------------------------------------------

class TestUpdate:
    """Tests for partial updates via RegistryPatch."""

    @pytest.mark.asyncio
    async def test_update_description(self, registry: SqliteRegistry) -> None:
        """Patching description leaves other fields unchanged."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="u", description="old"), ctx)
        await registry.update("u", RegistryPatch(description="new"))

        resolved = await registry.resolve("u", ctx)
        assert resolved.description == "new"
        assert resolved.kind == ComponentKind.AGENT  # unchanged

    @pytest.mark.asyncio
    async def test_update_missing_raises(self, registry: SqliteRegistry) -> None:
        """Updating a non-existent component raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            await registry.update("ghost", RegistryPatch(description="new"))

    @pytest.mark.asyncio
    async def test_update_enabled_flag(self, registry: SqliteRegistry) -> None:
        """Patching enabled to False disables the component."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="e"), ctx)
        await registry.update("e", RegistryPatch(enabled=False))

        resolved = await registry.resolve("e", ctx)
        assert resolved.enabled is False

    @pytest.mark.asyncio
    async def test_update_metadata(self, registry: SqliteRegistry) -> None:
        """Patching metadata replaces the entire metadata dict."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="m", metadata={"a": "1"}), ctx)
        await registry.update("m", RegistryPatch(metadata={"b": "2"}))

        resolved = await registry.resolve("m", ctx)
        assert resolved.metadata == {"b": "2"}

    @pytest.mark.asyncio
    async def test_update_none_fields_ignored(self, registry: SqliteRegistry) -> None:
        """None fields in the patch are not applied."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="n", description="keep"), ctx)
        await registry.update("n", RegistryPatch())  # all None

        resolved = await registry.resolve("n", ctx)
        assert resolved.description == "keep"


# ---------------------------------------------------------------------------
# Stats recording
# ---------------------------------------------------------------------------

class TestStats:
    """Tests for InvocationStats serialization through the registry."""

    @pytest.mark.asyncio
    async def test_stats_default(self, registry: SqliteRegistry) -> None:
        """Default stats have zero counters."""
        ctx = make_ctx()
        await registry.register(_make_entry(name="s"), ctx)
        resolved = await registry.resolve("s", ctx)
        assert resolved.stats.total_calls == 0
        assert resolved.stats.successes == 0
        assert resolved.stats.failures == 0

    @pytest.mark.asyncio
    async def test_stats_round_trip(self, registry: SqliteRegistry) -> None:
        """Custom stats survive serialization and deserialization."""
        ctx = make_ctx()
        entry = _make_entry(name="s")
        entry.stats = InvocationStats(
            total_calls=10,
            successes=8,
            failures=2,
            last_invoked_at=1234567890.0,
            avg_duration_ms=42.5,
        )
        await registry.register(entry, ctx)

        resolved = await registry.resolve("s", ctx)
        assert resolved.stats.total_calls == 10
        assert resolved.stats.successes == 8
        assert resolved.stats.failures == 2
        assert resolved.stats.last_invoked_at == 1234567890.0
        assert resolved.stats.avg_duration_ms == pytest.approx(42.5)


# ---------------------------------------------------------------------------
# Persistence (file-backed)
# ---------------------------------------------------------------------------

class TestPersistence:
    """Tests for data persistence across registry instances."""

    @pytest.mark.asyncio
    async def test_persist_across_close_reopen(self) -> None:
        """Data survives closing and reopening the registry."""
        ctx = make_ctx()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            reg1 = SqliteRegistry(db_path)
            await reg1.register(_make_entry(name="persistent", description="I survive"), ctx)
            reg1.close()

            reg2 = SqliteRegistry(db_path)
            resolved = await reg2.resolve("persistent", ctx)
            assert resolved is not None
            assert resolved.description == "I survive"
            reg2.close()
        finally:
            Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases: SQL injection, long names, special characters."""

    @pytest.mark.asyncio
    async def test_sql_injection_in_name(self, registry: SqliteRegistry) -> None:
        """SQL injection in name is safely handled by parameterized queries."""
        ctx = make_ctx()
        evil_name = "'; DROP TABLE components; --"
        entry = _make_entry(name=evil_name)
        await registry.register(entry, ctx)

        resolved = await registry.resolve(evil_name, ctx)
        assert resolved is not None
        assert resolved.name == evil_name

    @pytest.mark.asyncio
    async def test_sql_injection_in_description(self, registry: SqliteRegistry) -> None:
        """SQL injection in description is safely handled."""
        ctx = make_ctx()
        entry = _make_entry(name="safe", description="'; DROP TABLE components; --")
        await registry.register(entry, ctx)

        resolved = await registry.resolve("safe", ctx)
        assert resolved.description == "'; DROP TABLE components; --"

    @pytest.mark.asyncio
    async def test_very_long_name(self, registry: SqliteRegistry) -> None:
        """Very long names are stored and retrieved correctly."""
        ctx = make_ctx()
        long_name = "a" * 10_000
        entry = _make_entry(name=long_name)
        await registry.register(entry, ctx)

        resolved = await registry.resolve(long_name, ctx)
        assert resolved is not None
        assert resolved.name == long_name

    @pytest.mark.asyncio
    async def test_unicode_name(self, registry: SqliteRegistry) -> None:
        """Unicode names are handled correctly."""
        ctx = make_ctx()
        entry = _make_entry(name="\U0001f600-handler-\u00e9\u00e0\u00fc")
        await registry.register(entry, ctx)

        resolved = await registry.resolve("\U0001f600-handler-\u00e9\u00e0\u00fc", ctx)
        assert resolved is not None

    @pytest.mark.asyncio
    async def test_empty_string_name(self, registry: SqliteRegistry) -> None:
        """Empty string name can still be registered (no app-level validation)."""
        ctx = make_ctx()
        entry = _make_entry(name="")
        await registry.register(entry, ctx)

        resolved = await registry.resolve("", ctx)
        assert resolved is not None

    @pytest.mark.asyncio
    async def test_newlines_in_description(self, registry: SqliteRegistry) -> None:
        """Descriptions with newlines, tabs, and null bytes round-trip."""
        ctx = make_ctx()
        desc = "line1\nline2\ttab\x00null"
        entry = _make_entry(name="nl", description=desc)
        await registry.register(entry, ctx)

        resolved = await registry.resolve("nl", ctx)
        assert resolved.description == desc

    @pytest.mark.asyncio
    async def test_concurrent_register(self, registry: SqliteRegistry) -> None:
        """Multiple concurrent registrations don't corrupt data."""
        ctx = make_ctx()

        async def _register(i: int) -> None:
            """Register a single entry."""
            await registry.register(
                _make_entry(name=f"concurrent_{i}", description=f"handler {i}"), ctx
            )

        await asyncio.gather(*[_register(i) for i in range(20)])

        for i in range(20):
            resolved = await registry.resolve(f"concurrent_{i}", ctx)
            assert resolved is not None

    @pytest.mark.asyncio
    async def test_requirements_round_trip(self, registry: SqliteRegistry) -> None:
        """Requirements list survives serialization."""
        ctx = make_ctx()
        entry = _make_entry(name="deps", requirements=["cred:api_key", "component:logger"])
        await registry.register(entry, ctx)

        resolved = await registry.resolve("deps", ctx)
        assert resolved.requirements == ["cred:api_key", "component:logger"]

    @pytest.mark.asyncio
    async def test_permissions_round_trip(self, registry: SqliteRegistry) -> None:
        """Permissions list survives serialization."""
        ctx = make_ctx()
        entry = _make_entry(name="sec", permissions=["admin", "superuser"])
        await registry.register(entry, ctx)

        resolved = await registry.resolve("sec", ctx)
        assert set(resolved.permissions) == {"admin", "superuser"}
