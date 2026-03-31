"""In-memory registry — for testing and simple deployments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nerva.registry import (
    ComponentKind,
    HealthStatus,
    RegistryEntry,
    RegistryPatch,
)

if TYPE_CHECKING:
    from nerva.context import ExecContext


# Fields on RegistryEntry that RegistryPatch can overwrite.
_PATCHABLE_FIELDS = (
    "description",
    "metadata",
    "health",
    "enabled",
    "requirements",
    "permissions",
)


class InMemoryRegistry:
    """Registry backed by a plain dict. No persistence.

    Suitable for tests and single-process deployments where component
    registration does not need to survive restarts.

    ``discover()`` filters results by:
    - ``kind`` matches the requested component type
    - ``enabled`` is ``True``
    - ``health`` is not ``UNAVAILABLE``
    - If the entry declares ``permissions``, the caller must hold at
      least one matching role in ``ctx.permissions.roles``
    """

    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    async def register(self, entry: RegistryEntry, ctx: ExecContext) -> None:
        """Add or replace a component in the registry.

        If an entry with the same ``name`` already exists, it is overwritten.

        Args:
            entry: Component definition to register.
            ctx: Execution context carrying identity and permissions.
        """
        self._entries[entry.name] = entry

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
            List of matching ``RegistryEntry`` objects, sorted by name.
        """
        results: list[RegistryEntry] = []
        for entry in self._entries.values():
            if not _matches_discovery_criteria(entry, kind, ctx):
                continue
            results.append(entry)

        return sorted(results, key=lambda e: e.name)

    async def resolve(
        self, name: str, ctx: ExecContext
    ) -> RegistryEntry | None:
        """Look up a single component by name.

        Args:
            name: Unique component identifier.
            ctx: Execution context (reserved for future permission gating).

        Returns:
            The matching ``RegistryEntry``, or ``None`` if not found.
        """
        return self._entries.get(name)

    async def health(self, name: str) -> HealthStatus:
        """Get the current health status of a component.

        Args:
            name: Unique component identifier.

        Returns:
            Current ``HealthStatus``.

        Raises:
            KeyError: If no component with the given name is registered.
        """
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"Component not found: {name!r}")
        return entry.health

    async def update(self, name: str, patch: RegistryPatch) -> None:
        """Apply a partial update to a registered component.

        Only non-``None`` fields in the patch are written to the entry.

        Args:
            name: Unique component identifier to update.
            patch: Fields to overwrite.

        Raises:
            KeyError: If no component with the given name is registered.
        """
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"Component not found: {name!r}")

        _apply_patch(entry, patch)


def _matches_discovery_criteria(
    entry: RegistryEntry, kind: ComponentKind, ctx: ExecContext
) -> bool:
    """Check whether a registry entry passes all discovery filters.

    Args:
        entry: Candidate entry to evaluate.
        kind: Required component type.
        ctx: Execution context carrying caller permissions.

    Returns:
        ``True`` if the entry should be included in discovery results.
    """
    if entry.kind != kind:
        return False
    if not entry.enabled:
        return False
    if entry.health == HealthStatus.UNAVAILABLE:
        return False
    if entry.permissions and not _has_required_permission(entry, ctx):
        return False
    return True


def _has_required_permission(entry: RegistryEntry, ctx: ExecContext) -> bool:
    """Check that the caller holds at least one role required by the entry.

    Args:
        entry: Entry with a non-empty permissions list.
        ctx: Execution context carrying caller roles.

    Returns:
        ``True`` if the caller has at least one matching role.
    """
    caller_roles = ctx.permissions.roles
    return bool(caller_roles & frozenset(entry.permissions))


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
