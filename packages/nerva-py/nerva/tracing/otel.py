"""OpenTelemetry tracer adapter — maps Nerva spans/events to OTel spans.

Uses ``opentelemetry-api`` when available. Falls back gracefully to a no-op
when the package is not installed, allowing the adapter to be instantiated
without hard-failing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nerva.context import ExecContext, Event, Span

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional import — graceful fallback
# ---------------------------------------------------------------------------

_otel_available = False
_otel_trace = None
_otel_StatusCode = None

try:
    from opentelemetry import trace as _otel_trace_module
    from opentelemetry.trace import StatusCode as _StatusCodeClass

    _otel_trace = _otel_trace_module
    _otel_StatusCode = _StatusCodeClass
    _otel_available = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SERVICE_NAME = "nerva"
"""Default OTel service name when none is provided."""


# ---------------------------------------------------------------------------
# OTelTracer
# ---------------------------------------------------------------------------


def is_otel_available() -> bool:
    """Check whether the opentelemetry-api package is importable.

    Returns:
        ``True`` if ``opentelemetry.trace`` was successfully imported.
    """
    return _otel_available


class OTelTracer:
    """Maps Nerva tracing callbacks to OpenTelemetry spans and events.

    Each Nerva ``Span`` becomes an OTel span. Nerva ``Event`` objects are
    added as OTel span events on the current active span.

    When ``opentelemetry-api`` is not installed, all callbacks are no-ops.

    Args:
        service_name: OTel service name. Defaults to ``"nerva"``.
        resource_attributes: Extra key-value attributes added to every span.
    """

    def __init__(
        self,
        service_name: str = DEFAULT_SERVICE_NAME,
        resource_attributes: dict[str, str] | None = None,
    ) -> None:
        self._service_name = service_name
        self._resource_attributes = resource_attributes or {}
        self._active_spans: dict[str, object] = {}
        self._tracer = _create_tracer(service_name) if _otel_available else None

    def on_span_start(self, span: Span, ctx: ExecContext) -> None:
        """Start an OTel span corresponding to the Nerva span.

        Args:
            span: The Nerva span that just started.
            ctx: Execution context the span belongs to.
        """
        if self._tracer is None:
            return

        attributes = _build_span_attributes(span, ctx, self._resource_attributes)
        otel_span = self._tracer.start_span(
            name=span.name,
            attributes=attributes,
        )
        self._active_spans[span.span_id] = otel_span

    def on_span_end(self, span: Span, ctx: ExecContext) -> None:
        """End the OTel span corresponding to the Nerva span.

        Args:
            span: The Nerva span that just ended.
            ctx: Execution context the span belongs to.
        """
        otel_span = self._active_spans.pop(span.span_id, None)
        if otel_span is None:
            return

        _end_otel_span(otel_span)

    def on_event(self, event: Event, ctx: ExecContext) -> None:
        """Add an OTel span event for the Nerva event.

        Attaches the event to the most recently started active span, or
        skips if no spans are active.

        Args:
            event: The point-in-time event that was recorded.
            ctx: Execution context the event belongs to.
        """
        if not self._active_spans:
            return

        last_span = _get_last_active_span(self._active_spans)
        if last_span is None:
            return

        _add_event_to_span(last_span, event)

    def on_complete(self, ctx: ExecContext) -> None:
        """Flush any remaining active spans when the request completes.

        Args:
            ctx: The completed execution context.
        """
        for otel_span in list(self._active_spans.values()):
            _end_otel_span(otel_span)
        self._active_spans.clear()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _create_tracer(service_name: str) -> object | None:
    """Create an OTel tracer instance.

    Args:
        service_name: The OTel service name to register.

    Returns:
        An ``opentelemetry.trace.Tracer`` or ``None`` if OTel is unavailable.
    """
    if _otel_trace is None:
        return None
    return _otel_trace.get_tracer(service_name)


def _build_span_attributes(
    span: Span,
    ctx: ExecContext,
    extra: dict[str, str],
) -> dict[str, str]:
    """Build the attribute dict for an OTel span.

    Args:
        span: The Nerva span providing base attributes.
        ctx: Execution context for identity fields.
        extra: Additional resource attributes from the tracer config.

    Returns:
        Merged attribute dictionary.
    """
    attributes: dict[str, str] = {
        "nerva.request_id": ctx.request_id,
        "nerva.trace_id": ctx.trace_id,
        "nerva.span_id": span.span_id,
    }
    if span.parent_id is not None:
        attributes["nerva.parent_id"] = span.parent_id
    attributes.update(span.attributes)
    attributes.update(extra)
    return attributes


def _end_otel_span(otel_span: object) -> None:
    """End an OTel span safely.

    Args:
        otel_span: The OTel span object to end.
    """
    try:
        otel_span.end()  # type: ignore[union-attr]
    except Exception:
        logger.debug("Failed to end OTel span", exc_info=True)


def _get_last_active_span(active_spans: dict[str, object]) -> object | None:
    """Return the most recently added active span.

    Args:
        active_spans: Map of span_id to OTel span objects.

    Returns:
        The last span in insertion order, or ``None`` if empty.
    """
    if not active_spans:
        return None
    last_key = list(active_spans.keys())[-1]
    return active_spans[last_key]


def _add_event_to_span(otel_span: object, event: Event) -> None:
    """Add a Nerva Event as an OTel span event.

    Args:
        otel_span: The OTel span to attach the event to.
        event: The Nerva event to convert.
    """
    try:
        otel_span.add_event(  # type: ignore[union-attr]
            name=event.name,
            attributes=dict(event.attributes),
        )
    except Exception:
        logger.debug("Failed to add event to OTel span", exc_info=True)
