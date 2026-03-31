"""Cost tracking tracer — calculates USD cost from token usage.

Wraps any ``Tracer`` implementation and enriches span attributes with
``cost_usd`` computed from a per-model pricing configuration. Useful
for budget enforcement and cost visibility across delegated calls.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nerva.context import ExecContext, Event, Span, TokenUsage

if TYPE_CHECKING:
    from nerva.tracing import Tracer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COST_PER_1K_TOKENS = 0.0
"""Fallback cost when a model is not found in the pricing config."""

MODEL_ATTRIBUTE_KEY = "model"
"""Span attribute key used to look up the model name for pricing."""

COST_ATTRIBUTE_KEY = "cost_usd"
"""Span attribute key where the computed cost is stored."""


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------


ModelPricing = dict[str, float]
"""Mapping of model name to cost per 1,000 tokens (USD)."""


def calculate_cost(total_tokens: int, cost_per_1k: float) -> float:
    """Compute the USD cost for a given token count.

    Args:
        total_tokens: Number of tokens consumed.
        cost_per_1k: Cost in USD per 1,000 tokens.

    Returns:
        Computed cost in USD. Returns 0.0 for zero or negative tokens.
    """
    if total_tokens <= 0 or cost_per_1k <= 0:
        return 0.0
    return (total_tokens / 1000) * cost_per_1k


def lookup_model_cost(model_name: str | None, pricing: ModelPricing) -> float:
    """Look up the per-1k-token cost for a model.

    Args:
        model_name: The model identifier, or ``None`` if unknown.
        pricing: Pricing configuration mapping model names to costs.

    Returns:
        Cost per 1,000 tokens, or ``0.0`` if the model is unknown.
    """
    if model_name is None:
        return DEFAULT_COST_PER_1K_TOKENS
    return pricing.get(model_name, DEFAULT_COST_PER_1K_TOKENS)


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Tracer wrapper that calculates and records cost from token usage.

    Delegates all tracing callbacks to the wrapped tracer, and additionally
    computes ``cost_usd`` from the context's ``token_usage`` on span end
    and request completion.

    Args:
        inner: The tracer to delegate callbacks to.
        pricing: Mapping of model name to cost per 1,000 tokens (USD).
    """

    def __init__(self, inner: Tracer, pricing: ModelPricing) -> None:
        self._inner = inner
        self._pricing = pricing
        self._span_token_snapshots: dict[str, int] = {}

    def on_span_start(self, span: Span, ctx: ExecContext) -> None:
        """Record the token count at span start and delegate.

        Args:
            span: The span that just started.
            ctx: Execution context the span belongs to.
        """
        self._span_token_snapshots[span.span_id] = ctx.token_usage.total_tokens
        self._inner.on_span_start(span, ctx)

    def on_span_end(self, span: Span, ctx: ExecContext) -> None:
        """Compute cost for the span's token delta and delegate.

        Looks up the model from span attributes and calculates cost based
        on the difference in total tokens between span start and end.

        Args:
            span: The span that just ended.
            ctx: Execution context the span belongs to.
        """
        start_tokens = self._span_token_snapshots.pop(span.span_id, 0)
        delta_tokens = ctx.token_usage.total_tokens - start_tokens
        model_name = span.attributes.get(MODEL_ATTRIBUTE_KEY)
        cost_per_1k = lookup_model_cost(model_name, self._pricing)
        cost = calculate_cost(delta_tokens, cost_per_1k)

        ctx.add_event(
            "cost.calculated",
            span_id=span.span_id,
            model=model_name or "unknown",
            delta_tokens=str(delta_tokens),
            cost_usd=f"{cost:.6f}",
        )

        self._inner.on_span_end(span, ctx)

    def on_event(self, event: Event, ctx: ExecContext) -> None:
        """Delegate event callback to the inner tracer.

        Args:
            event: The point-in-time event that was recorded.
            ctx: Execution context the event belongs to.
        """
        self._inner.on_event(event, ctx)

    def on_complete(self, ctx: ExecContext) -> None:
        """Calculate total request cost and delegate.

        Uses the context's accumulated token usage to compute a final
        cost summary.

        Args:
            ctx: The completed execution context.
        """
        total_cost = self._calculate_total_cost(ctx)
        ctx.add_event(
            "cost.total",
            total_tokens=str(ctx.token_usage.total_tokens),
            cost_usd=f"{total_cost:.6f}",
        )
        self._inner.on_complete(ctx)

    def _calculate_total_cost(self, ctx: ExecContext) -> float:
        """Sum cost across all cost events in the context.

        Falls back to a generic calculation using total tokens if no
        cost events are present.

        Args:
            ctx: Execution context with accumulated token usage.

        Returns:
            Total cost in USD.
        """
        cost_events = [e for e in ctx.events if e.name == "cost.calculated"]
        if cost_events:
            return sum(
                float(e.attributes.get("cost_usd", "0")) for e in cost_events
            )
        # Fallback: no span-level cost events — use total tokens with zero cost
        return 0.0
