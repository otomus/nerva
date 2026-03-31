"""JSON log tracer — structured trace output to file or stderr.

Writes one JSON line per trace callback (span start, span end, event,
complete). Useful for local debugging, log aggregation pipelines, and
audit trails.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

from nerva.context import ExecContext, Event, Span


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPAN_START_TYPE = "span_start"
SPAN_END_TYPE = "span_end"
EVENT_TYPE = "event"
COMPLETE_TYPE = "complete"


class JsonLogTracer:
    """Writes trace events as JSON lines to a file or stream.

    Each span start/end and event produces one JSON line containing a
    timestamp, event type, request/trace identifiers, and relevant data
    fields from the span or event.

    Args:
        output: Where to write JSON lines. Accepts a file path (``str`` or
            ``Path``) which will be opened in append mode, or a writable
            ``TextIO`` stream. Defaults to ``sys.stderr``.
    """

    def __init__(self, output: str | Path | TextIO | None = None) -> None:
        self._owns_file = False
        self._stream = self._resolve_output(output)

    def on_span_start(self, span: Span, ctx: ExecContext) -> None:
        """Write a JSON line when a span begins.

        Args:
            span: The span that just started.
            ctx: Execution context the span belongs to.
        """
        payload = _build_span_payload(span)
        self._write_line(SPAN_START_TYPE, ctx, payload)

    def on_span_end(self, span: Span, ctx: ExecContext) -> None:
        """Write a JSON line when a span ends.

        Includes the span's duration in seconds.

        Args:
            span: The span that just ended.
            ctx: Execution context the span belongs to.
        """
        payload = _build_span_payload(span)
        if span.ended_at is not None:
            payload["duration_s"] = round(span.ended_at - span.started_at, 6)
        self._write_line(SPAN_END_TYPE, ctx, payload)

    def on_event(self, event: Event, ctx: ExecContext) -> None:
        """Write a JSON line when an event is recorded.

        Args:
            event: The point-in-time event.
            ctx: Execution context the event belongs to.
        """
        payload = _build_event_payload(event)
        self._write_line(EVENT_TYPE, ctx, payload)

    def on_complete(self, ctx: ExecContext) -> None:
        """Write a JSON line when a request completes.

        Includes summary fields: total span count, event count, and
        accumulated token usage.

        Args:
            ctx: The completed execution context.
        """
        payload = _build_complete_payload(ctx)
        self._write_line(COMPLETE_TYPE, ctx, payload)

    def close(self) -> None:
        """Close the underlying stream if this tracer owns it.

        Safe to call multiple times. Only closes file handles that were
        opened by this tracer (not caller-provided streams or stderr).
        """
        if self._owns_file and not self._stream.closed:
            self._stream.close()

    # -- Private helpers ----------------------------------------------------

    def _resolve_output(self, output: str | Path | TextIO | None) -> TextIO:
        """Turn the user-provided output target into a writable stream.

        Args:
            output: File path, stream, or ``None`` for stderr.

        Returns:
            A writable ``TextIO`` stream.
        """
        if output is None:
            return sys.stderr

        if isinstance(output, (str, Path)):
            path = Path(output)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._owns_file = True
            return open(path, "a", encoding="utf-8")  # noqa: SIM115

        return output

    def _write_line(
        self,
        event_type: str,
        ctx: ExecContext,
        payload: dict[str, object],
    ) -> None:
        """Serialize and write a single JSON line to the output stream.

        Args:
            event_type: One of the ``*_TYPE`` constants.
            ctx: Execution context for identity fields.
            payload: Type-specific data to merge into the line.
        """
        line = _build_base_record(event_type, ctx)
        line.update(payload)
        self._stream.write(json.dumps(line, default=str) + "\n")
        self._stream.flush()


# ---------------------------------------------------------------------------
# Pure helpers — build JSON-serialisable dicts
# ---------------------------------------------------------------------------


def _build_base_record(event_type: str, ctx: ExecContext) -> dict[str, object]:
    """Create the common envelope shared by every JSON line.

    Args:
        event_type: The trace event type string.
        ctx: Execution context to pull identifiers from.

    Returns:
        Dict with ``type``, ``timestamp``, ``request_id``, and ``trace_id``.
    """
    return {
        "type": event_type,
        "timestamp": time.time(),
        "request_id": ctx.request_id,
        "trace_id": ctx.trace_id,
    }


def _build_span_payload(span: Span) -> dict[str, object]:
    """Extract span fields into a JSON-ready dict.

    Args:
        span: The span to serialise.

    Returns:
        Dict containing span identity, name, timing, and attributes.
    """
    return {
        "span_id": span.span_id,
        "span_name": span.name,
        "parent_id": span.parent_id,
        "started_at": span.started_at,
        "ended_at": span.ended_at,
        "attributes": dict(span.attributes),
    }


def _build_event_payload(event: Event) -> dict[str, object]:
    """Extract event fields into a JSON-ready dict.

    Args:
        event: The event to serialise.

    Returns:
        Dict containing event name, timestamp, and attributes.
    """
    return {
        "event_name": event.name,
        "event_timestamp": event.timestamp,
        "attributes": dict(event.attributes),
    }


def _build_complete_payload(ctx: ExecContext) -> dict[str, object]:
    """Build summary data for the completion record.

    Args:
        ctx: The completed execution context.

    Returns:
        Dict with span/event counts and token usage breakdown.
    """
    return {
        "span_count": len(ctx.spans),
        "event_count": len(ctx.events),
        "token_usage": asdict(ctx.token_usage),
        "elapsed_s": round(ctx.elapsed_seconds(), 6),
    }
