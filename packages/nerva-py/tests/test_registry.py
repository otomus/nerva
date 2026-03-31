"""Tests for Registry and InMemoryRegistry — register, discover, resolve, update, health."""

from __future__ import annotations

import pytest

from nerva.registry import (
    ComponentKind,
    HealthStatus,
    InvocationStats,
    RegistryEntry,
    RegistryPatch,
    DURATION_SMOOTHING_FACTOR,
)
from nerva.registry.inmemory import InMemoryRegistry
from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    name: str = "search-agent",
    kind: ComponentKind = ComponentKind.AGENT,
    description: str = "Searches the web",
    *,
    enabled: bool = True,
    health: HealthStatus = HealthStatus.HEALTHY,
    permissions: list[str] | None = None,
) -> RegistryEntry:
    """Build a RegistryEntry with sensible defaults for tests."""
    return RegistryEntry(
        name=name,
        kind=kind,
        description=description,
        enabled=enabled,
        health=health,
        permissions=permissions or [],
    )


# ---------------------------------------------------------------------------
# register + resolve
# ---------------------------------------------------------------------------


class TestRegisterAndResolve:
    """Verify that register stores entries and resolve retrieves them."""

    @pytest.mark.asyncio
    async def test_register_and_resolve_by_name(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()
        entry = _make_entry(name="my-agent")

        await registry.register(entry, ctx)
        resolved = await registry.resolve("my-agent", ctx)

        assert resolved is not None
        assert resolved.name == "my-agent"
        assert resolved.kind == ComponentKind.AGENT

    @pytest.mark.asyncio
    async def test_resolve_unknown_name_returns_none(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()

        result = await registry.resolve("nonexistent", ctx)

        assert result is None

    @pytest.mark.asyncio
    async def test_register_duplicate_name_overwrites(self) -> None:
        """Registering with the same name should replace the previous entry."""
        registry = InMemoryRegistry()
        ctx = make_ctx()

        await registry.register(_make_entry(name="dup", description="first"), ctx)
        await registry.register(_make_entry(name="dup", description="second"), ctx)

        resolved = await registry.resolve("dup", ctx)
        assert resolved is not None
        assert resolved.description == "second"

    @pytest.mark.asyncio
    async def test_register_empty_description(self) -> None:
        """An entry with an empty description should still be storable."""
        registry = InMemoryRegistry()
        ctx = make_ctx()
        entry = _make_entry(name="blank", description="")

        await registry.register(entry, ctx)
        resolved = await registry.resolve("blank", ctx)

        assert resolved is not None
        assert resolved.description == ""

    @pytest.mark.asyncio
    async def test_register_very_long_name(self) -> None:
        """Stress test: a name with 10 000 characters should work."""
        registry = InMemoryRegistry()
        ctx = make_ctx()
        long_name = "a" * 10_000
        entry = _make_entry(name=long_name)

        await registry.register(entry, ctx)
        resolved = await registry.resolve(long_name, ctx)

        assert resolved is not None
        assert resolved.name == long_name


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


class TestDiscover:
    """Verify discover filters by kind, enabled, health, and permissions."""

    @pytest.mark.asyncio
    async def test_filters_by_kind(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()

        await registry.register(_make_entry(name="agent-1", kind=ComponentKind.AGENT), ctx)
        await registry.register(_make_entry(name="tool-1", kind=ComponentKind.TOOL), ctx)

        agents = await registry.discover(ComponentKind.AGENT, ctx)
        tools = await registry.discover(ComponentKind.TOOL, ctx)

        assert [e.name for e in agents] == ["agent-1"]
        assert [e.name for e in tools] == ["tool-1"]

    @pytest.mark.asyncio
    async def test_filters_out_disabled_entries(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()

        await registry.register(_make_entry(name="active", enabled=True), ctx)
        await registry.register(_make_entry(name="disabled", enabled=False), ctx)

        results = await registry.discover(ComponentKind.AGENT, ctx)

        assert [e.name for e in results] == ["active"]

    @pytest.mark.asyncio
    async def test_filters_out_unavailable_entries(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()

        await registry.register(
            _make_entry(name="healthy", health=HealthStatus.HEALTHY), ctx
        )
        await registry.register(
            _make_entry(name="degraded", health=HealthStatus.DEGRADED), ctx
        )
        await registry.register(
            _make_entry(name="down", health=HealthStatus.UNAVAILABLE), ctx
        )

        results = await registry.discover(ComponentKind.AGENT, ctx)
        names = [e.name for e in results]

        assert "healthy" in names
        assert "degraded" in names
        assert "down" not in names

    @pytest.mark.asyncio
    async def test_filters_by_permissions(self) -> None:
        """Only entries whose permissions overlap with ctx.permissions.roles are returned."""
        registry = InMemoryRegistry()

        admin_ctx = make_ctx(roles=frozenset({"admin"}))
        user_ctx = make_ctx(roles=frozenset({"user"}))
        empty_ctx = make_ctx(roles=frozenset())

        await registry.register(
            _make_entry(name="admin-only", permissions=["admin"]), admin_ctx
        )
        await registry.register(
            _make_entry(name="public", permissions=[]), admin_ctx
        )

        admin_results = await registry.discover(ComponentKind.AGENT, admin_ctx)
        user_results = await registry.discover(ComponentKind.AGENT, user_ctx)
        empty_results = await registry.discover(ComponentKind.AGENT, empty_ctx)

        assert {e.name for e in admin_results} == {"admin-only", "public"}
        # "user" role doesn't match "admin" permission, but "public" has no perms
        assert {e.name for e in user_results} == {"public"}
        assert {e.name for e in empty_results} == {"public"}

    @pytest.mark.asyncio
    async def test_no_matching_entries_returns_empty(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()

        results = await registry.discover(ComponentKind.PLUGIN, ctx)

        assert results == []

    @pytest.mark.asyncio
    async def test_discover_results_sorted_by_name(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()

        for name in ["zebra", "alpha", "middle"]:
            await registry.register(_make_entry(name=name), ctx)

        results = await registry.discover(ComponentKind.AGENT, ctx)

        assert [e.name for e in results] == ["alpha", "middle", "zebra"]


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class TestHealth:
    """Verify health() returns correct status and raises on unknown name."""

    @pytest.mark.asyncio
    async def test_returns_correct_status(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()

        await registry.register(
            _make_entry(name="ok", health=HealthStatus.HEALTHY), ctx
        )
        await registry.register(
            _make_entry(name="bad", health=HealthStatus.DEGRADED), ctx
        )

        assert await registry.health("ok") == HealthStatus.HEALTHY
        assert await registry.health("bad") == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_unknown_name_raises_key_error(self) -> None:
        registry = InMemoryRegistry()

        with pytest.raises(KeyError, match="ghost"):
            await registry.health("ghost")


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


class TestUpdate:
    """Verify update() applies partial patches and raises on unknown name."""

    @pytest.mark.asyncio
    async def test_applies_non_none_patch_fields(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()
        await registry.register(_make_entry(name="target", description="old"), ctx)

        patch = RegistryPatch(description="new", enabled=False)
        await registry.update("target", patch)

        entry = await registry.resolve("target", ctx)
        assert entry is not None
        assert entry.description == "new"
        assert entry.enabled is False
        # Fields not in patch remain unchanged
        assert entry.health == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_patch_with_all_none_changes_nothing(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()
        await registry.register(_make_entry(name="stable", description="orig"), ctx)

        await registry.update("stable", RegistryPatch())

        entry = await registry.resolve("stable", ctx)
        assert entry is not None
        assert entry.description == "orig"

    @pytest.mark.asyncio
    async def test_unknown_name_raises_key_error(self) -> None:
        registry = InMemoryRegistry()

        with pytest.raises(KeyError, match="missing"):
            await registry.update("missing", RegistryPatch(description="x"))

    @pytest.mark.asyncio
    async def test_update_health_via_patch(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()
        await registry.register(_make_entry(name="node"), ctx)

        await registry.update("node", RegistryPatch(health=HealthStatus.UNAVAILABLE))

        assert await registry.health("node") == HealthStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_update_permissions_via_patch(self) -> None:
        registry = InMemoryRegistry()
        ctx = make_ctx()
        await registry.register(_make_entry(name="gated", permissions=[]), ctx)

        await registry.update("gated", RegistryPatch(permissions=["admin", "ops"]))

        entry = await registry.resolve("gated", ctx)
        assert entry is not None
        assert entry.permissions == ["admin", "ops"]


# ---------------------------------------------------------------------------
# InvocationStats
# ---------------------------------------------------------------------------


class TestInvocationStats:
    """Verify record_success / record_failure update counts and EMA duration."""

    def test_record_success_increments_counts(self) -> None:
        stats = InvocationStats()

        stats.record_success(100.0)

        assert stats.total_calls == 1
        assert stats.successes == 1
        assert stats.failures == 0
        assert stats.last_invoked_at is not None

    def test_record_failure_increments_counts(self) -> None:
        stats = InvocationStats()

        stats.record_failure(50.0)

        assert stats.total_calls == 1
        assert stats.successes == 0
        assert stats.failures == 1

    def test_first_call_sets_avg_duration_directly(self) -> None:
        stats = InvocationStats()

        stats.record_success(200.0)

        assert stats.avg_duration_ms == 200.0

    def test_subsequent_calls_use_ema(self) -> None:
        stats = InvocationStats()

        stats.record_success(100.0)  # avg = 100
        stats.record_success(200.0)  # avg = 0.2 * 200 + 0.8 * 100 = 120

        alpha = DURATION_SMOOTHING_FACTOR
        expected = alpha * 200.0 + (1 - alpha) * 100.0
        assert stats.avg_duration_ms == pytest.approx(expected)

    def test_mixed_success_and_failure(self) -> None:
        stats = InvocationStats()

        stats.record_success(10.0)
        stats.record_failure(20.0)
        stats.record_success(30.0)

        assert stats.total_calls == 3
        assert stats.successes == 2
        assert stats.failures == 1

    def test_zero_duration(self) -> None:
        """Duration of 0ms should be accepted without error."""
        stats = InvocationStats()

        stats.record_success(0.0)

        assert stats.avg_duration_ms == 0.0
        assert stats.total_calls == 1

    def test_very_large_duration(self) -> None:
        """Extremely large durations should not cause overflow."""
        stats = InvocationStats()

        stats.record_success(1e15)

        assert stats.avg_duration_ms == 1e15
