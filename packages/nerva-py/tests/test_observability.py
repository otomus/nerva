"""Tests for observability — OTel adapter, cost tracking, edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nerva.context import ExecContext, Span, Event, TokenUsage
from nerva.tracing.cost import (
    CostTracker,
    ModelPricing,
    calculate_cost,
    lookup_model_cost,
    DEFAULT_COST_PER_1K_TOKENS,
)
from nerva.tracing.otel import OTelTracer, is_otel_available

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span(
    name: str = "test.span",
    span_id: str = "span-1",
    parent_id: str | None = None,
    started_at: float = 1000.0,
    ended_at: float | None = None,
    attributes: dict[str, str] | None = None,
) -> Span:
    """Build a Span with sensible defaults."""
    return Span(
        span_id=span_id,
        name=name,
        parent_id=parent_id,
        started_at=started_at,
        ended_at=ended_at,
        attributes=attributes or {},
    )


def _make_event(
    name: str = "test.event",
    attributes: dict[str, str] | None = None,
) -> Event:
    """Build an Event with sensible defaults."""
    return Event(
        timestamp=1000.0,
        name=name,
        attributes=attributes or {},
    )


class FakeTracer:
    """Records all tracing callbacks for assertion."""

    def __init__(self) -> None:
        self.span_starts: list[Span] = []
        self.span_ends: list[Span] = []
        self.events: list[Event] = []
        self.completes: list[ExecContext] = []

    def on_span_start(self, span: Span, ctx: ExecContext) -> None:
        """Record span start."""
        self.span_starts.append(span)

    def on_span_end(self, span: Span, ctx: ExecContext) -> None:
        """Record span end."""
        self.span_ends.append(span)

    def on_event(self, event: Event, ctx: ExecContext) -> None:
        """Record event."""
        self.events.append(event)

    def on_complete(self, ctx: ExecContext) -> None:
        """Record completion."""
        self.completes.append(ctx)


# ---------------------------------------------------------------------------
# N-650: OTel adapter
# ---------------------------------------------------------------------------


class TestOTelTracer:
    """Verify OTelTracer maps Nerva spans/events to OTel calls."""

    def test_otel_availability_check(self) -> None:
        """is_otel_available returns a boolean."""
        result = is_otel_available()
        assert isinstance(result, bool)

    def test_otel_tracer_instantiates_without_otel(self) -> None:
        """OTelTracer can be created even if opentelemetry is not installed."""
        tracer = OTelTracer(service_name="test-service")
        assert tracer is not None

    def test_on_span_start_without_otel_is_noop(self) -> None:
        """on_span_start does not crash when OTel is unavailable."""
        tracer = OTelTracer()
        ctx = make_ctx()
        span = _make_span()

        # Should not raise
        tracer.on_span_start(span, ctx)

    def test_on_span_end_without_otel_is_noop(self) -> None:
        """on_span_end does not crash when OTel is unavailable."""
        tracer = OTelTracer()
        ctx = make_ctx()
        span = _make_span(ended_at=1001.0)

        tracer.on_span_end(span, ctx)

    def test_on_event_without_otel_is_noop(self) -> None:
        """on_event does not crash when OTel is unavailable."""
        tracer = OTelTracer()
        ctx = make_ctx()
        event = _make_event()

        tracer.on_event(event, ctx)

    def test_on_complete_without_otel_is_noop(self) -> None:
        """on_complete does not crash when OTel is unavailable."""
        tracer = OTelTracer()
        ctx = make_ctx()

        tracer.on_complete(ctx)

    def test_custom_service_name(self) -> None:
        """OTelTracer stores the service name."""
        tracer = OTelTracer(service_name="my-service")
        assert tracer._service_name == "my-service"

    def test_custom_resource_attributes(self) -> None:
        """OTelTracer stores resource attributes."""
        attrs = {"env": "test", "version": "1.0"}
        tracer = OTelTracer(resource_attributes=attrs)
        assert tracer._resource_attributes == attrs

    def test_on_complete_clears_active_spans(self) -> None:
        """on_complete flushes any remaining active spans."""
        tracer = OTelTracer()
        ctx = make_ctx()
        span = _make_span()

        tracer.on_span_start(span, ctx)
        tracer.on_complete(ctx)

        assert len(tracer._active_spans) == 0

    def test_on_span_end_removes_from_active(self) -> None:
        """on_span_end removes the span from the active set."""
        tracer = OTelTracer()
        ctx = make_ctx()
        span = _make_span()

        tracer.on_span_start(span, ctx)
        tracer.on_span_end(span, ctx)

        assert "span-1" not in tracer._active_spans

    def test_on_event_with_no_active_spans(self) -> None:
        """on_event is safe when no spans are active."""
        tracer = OTelTracer()
        ctx = make_ctx()
        event = _make_event()

        # Should not raise
        tracer.on_event(event, ctx)


# ---------------------------------------------------------------------------
# N-652: Cost tracking
# ---------------------------------------------------------------------------


SAMPLE_PRICING: ModelPricing = {
    "gpt-4": 0.03,
    "gpt-3.5-turbo": 0.002,
    "claude-3-opus": 0.015,
}


class TestCostCalculation:
    """Verify pure cost calculation functions."""

    def test_basic_cost_calculation(self) -> None:
        """calculate_cost computes correctly for normal inputs."""
        cost = calculate_cost(1000, 0.03)
        assert abs(cost - 0.03) < 1e-9

    def test_cost_for_500_tokens(self) -> None:
        """500 tokens at $0.03/1k = $0.015."""
        cost = calculate_cost(500, 0.03)
        assert abs(cost - 0.015) < 1e-9

    def test_zero_tokens_returns_zero(self) -> None:
        """Zero tokens always yields zero cost."""
        assert calculate_cost(0, 0.03) == 0.0

    def test_negative_tokens_returns_zero(self) -> None:
        """Negative token count yields zero cost."""
        assert calculate_cost(-100, 0.03) == 0.0

    def test_zero_cost_per_1k_returns_zero(self) -> None:
        """Zero cost per 1k yields zero regardless of tokens."""
        assert calculate_cost(5000, 0.0) == 0.0

    def test_negative_cost_per_1k_returns_zero(self) -> None:
        """Negative cost per 1k yields zero."""
        assert calculate_cost(5000, -0.01) == 0.0

    def test_large_token_count(self) -> None:
        """Large token counts compute without overflow."""
        cost = calculate_cost(1_000_000, 0.03)
        assert abs(cost - 30.0) < 1e-6

    def test_lookup_known_model(self) -> None:
        """lookup_model_cost returns the configured price."""
        cost = lookup_model_cost("gpt-4", SAMPLE_PRICING)
        assert cost == 0.03

    def test_lookup_unknown_model(self) -> None:
        """lookup_model_cost returns default for unknown models."""
        cost = lookup_model_cost("unknown-model", SAMPLE_PRICING)
        assert cost == DEFAULT_COST_PER_1K_TOKENS

    def test_lookup_none_model(self) -> None:
        """lookup_model_cost returns default for None model."""
        cost = lookup_model_cost(None, SAMPLE_PRICING)
        assert cost == DEFAULT_COST_PER_1K_TOKENS

    def test_lookup_empty_pricing(self) -> None:
        """lookup_model_cost returns default when pricing dict is empty."""
        cost = lookup_model_cost("gpt-4", {})
        assert cost == DEFAULT_COST_PER_1K_TOKENS


class TestCostTracker:
    """Verify CostTracker wraps a tracer and computes costs."""

    def test_delegates_span_start(self) -> None:
        """CostTracker forwards on_span_start to inner tracer."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()
        span = _make_span()

        tracker.on_span_start(span, ctx)

        assert len(inner.span_starts) == 1
        assert inner.span_starts[0] is span

    def test_delegates_span_end(self) -> None:
        """CostTracker forwards on_span_end to inner tracer."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()
        span = _make_span(ended_at=1001.0)

        tracker.on_span_start(span, ctx)
        tracker.on_span_end(span, ctx)

        assert len(inner.span_ends) == 1

    def test_delegates_event(self) -> None:
        """CostTracker forwards on_event to inner tracer."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()
        event = _make_event()

        tracker.on_event(event, ctx)

        assert len(inner.events) == 1
        assert inner.events[0] is event

    def test_delegates_complete(self) -> None:
        """CostTracker forwards on_complete to inner tracer."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()

        tracker.on_complete(ctx)

        assert len(inner.completes) == 1

    def test_span_end_emits_cost_event(self) -> None:
        """on_span_end emits a cost.calculated event with the computed cost."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()

        span = _make_span(attributes={"model": "gpt-4"})
        tracker.on_span_start(span, ctx)

        # Simulate token usage during the span
        ctx.record_tokens(TokenUsage(prompt_tokens=500, completion_tokens=500, total_tokens=1000))

        tracker.on_span_end(span, ctx)

        cost_events = [e for e in ctx.events if e.name == "cost.calculated"]
        assert len(cost_events) == 1
        assert cost_events[0].attributes["model"] == "gpt-4"
        assert cost_events[0].attributes["delta_tokens"] == "1000"
        assert float(cost_events[0].attributes["cost_usd"]) == pytest.approx(0.03)

    def test_span_end_unknown_model_zero_cost(self) -> None:
        """Span with unknown model gets zero cost."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()

        span = _make_span(attributes={"model": "unknown-model"})
        tracker.on_span_start(span, ctx)
        ctx.record_tokens(TokenUsage(total_tokens=1000))
        tracker.on_span_end(span, ctx)

        cost_events = [e for e in ctx.events if e.name == "cost.calculated"]
        assert len(cost_events) == 1
        assert float(cost_events[0].attributes["cost_usd"]) == 0.0

    def test_span_end_no_model_attribute(self) -> None:
        """Span without model attribute uses default (zero) pricing."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()

        span = _make_span()  # no model attribute
        tracker.on_span_start(span, ctx)
        ctx.record_tokens(TokenUsage(total_tokens=500))
        tracker.on_span_end(span, ctx)

        cost_events = [e for e in ctx.events if e.name == "cost.calculated"]
        assert len(cost_events) == 1
        assert cost_events[0].attributes["model"] == "unknown"

    def test_token_accumulation_across_spans(self) -> None:
        """Cost tracks token deltas correctly across multiple spans."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()

        # First span: 500 tokens
        span1 = _make_span(span_id="s1", attributes={"model": "gpt-4"})
        tracker.on_span_start(span1, ctx)
        ctx.record_tokens(TokenUsage(total_tokens=500))
        tracker.on_span_end(span1, ctx)

        # Second span: 300 more tokens (total now 800, delta = 300)
        span2 = _make_span(span_id="s2", attributes={"model": "gpt-3.5-turbo"})
        tracker.on_span_start(span2, ctx)
        ctx.record_tokens(TokenUsage(total_tokens=300))
        tracker.on_span_end(span2, ctx)

        cost_events = [e for e in ctx.events if e.name == "cost.calculated"]
        assert len(cost_events) == 2

        # First span: 500 tokens * $0.03/1k = $0.015
        assert cost_events[0].attributes["delta_tokens"] == "500"
        assert float(cost_events[0].attributes["cost_usd"]) == pytest.approx(0.015)

        # Second span: 300 tokens * $0.002/1k = $0.0006
        assert cost_events[1].attributes["delta_tokens"] == "300"
        assert float(cost_events[1].attributes["cost_usd"]) == pytest.approx(0.0006)

    def test_on_complete_emits_total_cost(self) -> None:
        """on_complete emits a cost.total event summarizing all costs."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()

        span = _make_span(attributes={"model": "gpt-4"})
        tracker.on_span_start(span, ctx)
        ctx.record_tokens(TokenUsage(total_tokens=2000))
        tracker.on_span_end(span, ctx)

        tracker.on_complete(ctx)

        total_events = [e for e in ctx.events if e.name == "cost.total"]
        assert len(total_events) == 1
        assert float(total_events[0].attributes["cost_usd"]) == pytest.approx(0.06)

    def test_on_complete_zero_tokens(self) -> None:
        """on_complete with zero tokens emits zero cost."""
        inner = FakeTracer()
        tracker = CostTracker(inner, SAMPLE_PRICING)
        ctx = make_ctx()

        tracker.on_complete(ctx)

        total_events = [e for e in ctx.events if e.name == "cost.total"]
        assert len(total_events) == 1
        assert float(total_events[0].attributes["cost_usd"]) == 0.0

    def test_missing_pricing_config(self) -> None:
        """CostTracker with empty pricing yields zero cost for all models."""
        inner = FakeTracer()
        tracker = CostTracker(inner, {})
        ctx = make_ctx()

        span = _make_span(attributes={"model": "gpt-4"})
        tracker.on_span_start(span, ctx)
        ctx.record_tokens(TokenUsage(total_tokens=1000))
        tracker.on_span_end(span, ctx)

        cost_events = [e for e in ctx.events if e.name == "cost.calculated"]
        assert float(cost_events[0].attributes["cost_usd"]) == 0.0
