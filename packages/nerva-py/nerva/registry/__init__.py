"""Registry — unified catalog of agents, tools, and components."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from nerva.context import ExecContext


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ComponentKind(StrEnum):
    """Classification of a registered component.

    Members:
        AGENT: An agent handler that processes user input.
        TOOL: A tool invocable by agents (e.g. MCP tool).
        SENSE: A sensory input processor (e.g. vision, hearing).
        PLUGIN: An extension that hooks into lifecycle events.
    """

    AGENT = "agent"
    TOOL = "tool"
    SENSE = "sense"
    PLUGIN = "plugin"


class HealthStatus(StrEnum):
    """Operational health of a registered component.

    Members:
        HEALTHY: Fully operational.
        DEGRADED: Operational with reduced capability or elevated error rate.
        UNAVAILABLE: Not accepting invocations.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# InvocationStats
# ---------------------------------------------------------------------------

DURATION_SMOOTHING_FACTOR = 0.2
"""Exponential moving average weight for new duration observations."""


@dataclass
class InvocationStats:
    """Tracks invocation metrics for a registered component.

    Uses an exponential moving average for ``avg_duration_ms`` so that
    recent latency is weighted more heavily than historical.

    Attributes:
        total_calls: Total number of invocations (success + failure).
        successes: Number of successful invocations.
        failures: Number of failed invocations.
        last_invoked_at: Unix timestamp of the most recent invocation, or ``None``.
        avg_duration_ms: Exponential moving average of invocation duration.
    """

    total_calls: int = 0
    successes: int = 0
    failures: int = 0
    last_invoked_at: float | None = None
    avg_duration_ms: float = 0.0

    def record_success(self, duration_ms: float) -> None:
        """Record a successful invocation.

        Args:
            duration_ms: Wall-clock duration of the invocation in milliseconds.
        """
        self.total_calls += 1
        self.successes += 1
        self.last_invoked_at = time.time()
        self._update_avg_duration(duration_ms)

    def record_failure(self, duration_ms: float) -> None:
        """Record a failed invocation.

        Args:
            duration_ms: Wall-clock duration of the invocation in milliseconds.
        """
        self.total_calls += 1
        self.failures += 1
        self.last_invoked_at = time.time()
        self._update_avg_duration(duration_ms)

    def _update_avg_duration(self, duration_ms: float) -> None:
        """Update the exponential moving average of duration.

        On the first call, sets the average directly. On subsequent calls,
        blends the new observation using ``DURATION_SMOOTHING_FACTOR``.

        Args:
            duration_ms: Latest observed duration in milliseconds.
        """
        if self.total_calls <= 1:
            self.avg_duration_ms = duration_ms
            return

        alpha = DURATION_SMOOTHING_FACTOR
        self.avg_duration_ms = (
            alpha * duration_ms + (1 - alpha) * self.avg_duration_ms
        )


# ---------------------------------------------------------------------------
# RegistryEntry
# ---------------------------------------------------------------------------


@dataclass
class RegistryEntry:
    """A registered component in the catalog.

    Attributes:
        name: Unique identifier for the component.
        kind: Component type (agent, tool, sense, plugin).
        description: What it does — used by the router for matching.
        schema: Input/output JSON schema (primarily for tools), or ``None``.
        metadata: Custom key-value fields (role, origin, version, etc.).
        health: Current operational health status.
        stats: Invocation metrics tracked over the component's lifetime.
        enabled: Whether the component is active. Can be disabled without removal.
        requirements: Dependencies — credential names, other component names.
        permissions: Role names required to access this component.
    """

    name: str
    kind: ComponentKind
    description: str
    schema: dict[str, object] | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    health: HealthStatus = HealthStatus.HEALTHY
    stats: InvocationStats = field(default_factory=InvocationStats)
    enabled: bool = True
    requirements: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RegistryPatch
# ---------------------------------------------------------------------------


@dataclass
class RegistryPatch:
    """Partial update for a registry entry.

    Only non-``None`` fields are applied when passed to ``Registry.update()``.

    Attributes:
        description: New description, or ``None`` to leave unchanged.
        metadata: New metadata dict, or ``None`` to leave unchanged.
        health: New health status, or ``None`` to leave unchanged.
        enabled: New enabled flag, or ``None`` to leave unchanged.
        requirements: New requirements list, or ``None`` to leave unchanged.
        permissions: New permissions list, or ``None`` to leave unchanged.
    """

    description: str | None = None
    metadata: dict[str, str] | None = None
    health: HealthStatus | None = None
    enabled: bool | None = None
    requirements: list[str] | None = None
    permissions: list[str] | None = None


# ---------------------------------------------------------------------------
# Registry protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Registry(Protocol):
    """Unified catalog of agents, tools, senses, and plugins.

    Implementations may be backed by an in-memory dict, a database, or
    a remote service. All methods accept an ``ExecContext`` so that
    permission checks and observability flow through naturally.
    """

    async def register(self, entry: RegistryEntry, ctx: ExecContext) -> None:
        """Add or replace a component in the registry.

        If an entry with the same ``name`` already exists, it is overwritten.

        Args:
            entry: Component definition to register.
            ctx: Execution context carrying identity and permissions.
        """
        ...

    async def discover(
        self, kind: ComponentKind, ctx: ExecContext
    ) -> list[RegistryEntry]:
        """List components of a given kind visible to the caller.

        Filters out disabled entries, unavailable entries, and entries
        whose required permissions are not satisfied by ``ctx.permissions``.

        Args:
            kind: Component type to filter by.
            ctx: Execution context used for permission checks.

        Returns:
            List of matching ``RegistryEntry`` objects.
        """
        ...

    async def resolve(
        self, name: str, ctx: ExecContext
    ) -> RegistryEntry | None:
        """Look up a single component by name.

        Args:
            name: Unique component identifier.
            ctx: Execution context (for future permission gating).

        Returns:
            The matching ``RegistryEntry``, or ``None`` if not found.
        """
        ...

    async def health(self, name: str) -> HealthStatus:
        """Get the current health status of a component.

        Args:
            name: Unique component identifier.

        Returns:
            Current ``HealthStatus``.

        Raises:
            KeyError: If no component with the given name is registered.
        """
        ...

    async def update(self, name: str, patch: RegistryPatch) -> None:
        """Apply a partial update to a registered component.

        Only non-``None`` fields in the patch are written.

        Args:
            name: Unique component identifier to update.
            patch: Fields to overwrite.

        Raises:
            KeyError: If no component with the given name is registered.
        """
        ...


__all__ = [
    "ComponentKind",
    "HealthStatus",
    "InvocationStats",
    "RegistryEntry",
    "RegistryPatch",
    "Registry",
    "DURATION_SMOOTHING_FACTOR",
]
