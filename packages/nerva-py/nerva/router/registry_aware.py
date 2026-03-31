"""Registry-aware router wrapper — filters handler candidates by registry health."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nerva.registry import ComponentKind, HealthStatus
from nerva.router import HandlerCandidate, IntentResult

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.registry import Registry
    from nerva.router import IntentRouter


class RegistryAwareRouter:
    """Wraps any ``IntentRouter`` and filters candidates against registry health.

    After the inner router classifies, this wrapper removes candidates whose
    corresponding registry entry is unavailable or not found. This prevents
    the orchestrator from dispatching to unhealthy handlers.

    Attributes:
        _inner: The wrapped router that performs classification.
        _registry: Registry used to check handler health status.
    """

    def __init__(self, inner: IntentRouter, registry: Registry) -> None:
        self._inner = inner
        self._registry = registry

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Classify intent and filter handlers by registry health.

        Delegates to the inner router for classification, then removes
        any candidate whose registry entry is unavailable or missing.

        Args:
            message: Raw user message text.
            ctx: Execution context carrying permissions and trace.

        Returns:
            An ``IntentResult`` with only healthy/degraded handler candidates.
        """
        result = await self._inner.classify(message, ctx)
        filtered = await self._filter_healthy_candidates(result.handlers, ctx)
        return IntentResult(
            intent=result.intent,
            confidence=result.confidence,
            handlers=filtered,
            raw_scores=result.raw_scores,
        )

    async def _filter_healthy_candidates(
        self, candidates: list[HandlerCandidate], ctx: ExecContext
    ) -> list[HandlerCandidate]:
        """Remove candidates whose registry entry is unavailable or absent.

        A candidate is kept if:
        - It has a registry entry with health != UNAVAILABLE, OR
        - It has no registry entry (unregistered handlers pass through).

        Args:
            candidates: Handler candidates from the inner router.
            ctx: Execution context for registry lookups.

        Returns:
            Filtered list preserving original order.
        """
        healthy: list[HandlerCandidate] = []
        for candidate in candidates:
            if await self._is_candidate_available(candidate.name, ctx):
                healthy.append(candidate)
        return healthy

    async def _is_candidate_available(self, name: str, ctx: ExecContext) -> bool:
        """Check whether a handler is available in the registry.

        Returns ``True`` for handlers not found in the registry (they are
        not registry-managed, so we don't block them).

        Args:
            name: Handler name to look up.
            ctx: Execution context for registry resolve.

        Returns:
            ``True`` if the handler is available or unregistered.
        """
        entry = await self._registry.resolve(name, ctx)
        if entry is None:
            return True
        return entry.health != HealthStatus.UNAVAILABLE
