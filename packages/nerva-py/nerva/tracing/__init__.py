"""Tracing — observe execution flow across primitives.

Defines the ``Tracer`` protocol that all tracer implementations must satisfy.
Tracers receive callbacks for span lifecycle events, discrete events, and
request completion — enabling structured logging, metrics export, and
distributed tracing integrations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from nerva.context import ExecContext, Event, Span


@runtime_checkable
class Tracer(Protocol):
    """Observes execution spans and events from the pipeline.

    Implement this protocol to receive structured callbacks as requests
    flow through Nerva primitives. Multiple tracers can be active
    simultaneously (e.g. one for JSON logging, one for metrics).
    """

    def on_span_start(self, span: Span, ctx: ExecContext) -> None:
        """Called when a new span begins.

        Args:
            span: The span that just started (``ended_at`` will be ``None``).
            ctx: Execution context the span belongs to.
        """
        ...

    def on_span_end(self, span: Span, ctx: ExecContext) -> None:
        """Called when a span completes.

        Args:
            span: The span that just ended (``ended_at`` will be populated).
            ctx: Execution context the span belongs to.
        """
        ...

    def on_event(self, event: Event, ctx: ExecContext) -> None:
        """Called when a structured event is recorded.

        Args:
            event: The point-in-time event that was recorded.
            ctx: Execution context the event belongs to.
        """
        ...

    def on_complete(self, ctx: ExecContext) -> None:
        """Called when the entire request completes.

        This fires once per root context, after all spans have ended.
        Use it for flushing buffers, emitting summary metrics, or
        closing file handles.

        Args:
            ctx: The completed execution context with final token usage and spans.
        """
        ...
