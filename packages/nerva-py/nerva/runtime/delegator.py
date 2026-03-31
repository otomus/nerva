"""SubprocessDelegator — cross-language delegation via child process.

Spawns a command (e.g. a Node.js agent) as a subprocess, passing the
message and serialised ``ExecContext`` as JSON on stdin. The child process
writes an ``AgentResult``-shaped JSON object to stdout.

This enables a Python orchestrator to delegate to handlers implemented
in other languages (Node.js, Go, etc.) while preserving context lineage.

Task: N-632.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

from nerva.context import ExecContext, TokenUsage
from nerva.runtime import AgentInput, AgentResult, AgentStatus


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DELEGATOR_TIMEOUT_SECONDS = 30.0
"""Maximum wall-clock seconds the subprocess may run."""


# ---------------------------------------------------------------------------
# Context serialisation helpers
# ---------------------------------------------------------------------------


def _serialize_context(ctx: ExecContext) -> dict[str, Any]:
    """Serialize the essential fields of an ExecContext to a JSON-safe dict.

    Only includes fields that are meaningful for a cross-process child:
    request_id, trace_id, user_id, session_id, depth, and metadata.
    Permissions and cancellation signals cannot cross process boundaries.

    Args:
        ctx: The execution context to serialize.

    Returns:
        A dict suitable for ``json.dumps``.
    """
    return {
        "request_id": ctx.request_id,
        "trace_id": ctx.trace_id,
        "user_id": ctx.user_id,
        "session_id": ctx.session_id,
        "depth": ctx.depth,
        "metadata": dict(ctx.metadata),
    }


def _parse_result(raw: str, handler: str) -> AgentResult:
    """Parse subprocess stdout into an AgentResult.

    Attempts to decode the output as a JSON object with ``status`` and
    ``output`` fields. Falls back to a SUCCESS result with the raw text
    as output, or an ERROR result if the output is empty.

    Args:
        raw: Raw stdout from the subprocess.
        handler: Handler name for the result.

    Returns:
        Parsed ``AgentResult``.
    """
    stripped = raw.strip()
    if not stripped:
        return AgentResult(
            status=AgentStatus.ERROR,
            error="subprocess produced no output",
            handler=handler,
        )

    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return AgentResult(
            status=AgentStatus.SUCCESS,
            output=stripped,
            handler=handler,
        )

    if not isinstance(data, dict):
        return AgentResult(
            status=AgentStatus.SUCCESS,
            output=stripped,
            handler=handler,
        )

    return AgentResult(
        status=AgentStatus(data.get("status", "success")),
        output=data.get("output", ""),
        data=data.get("data", {}),
        error=data.get("error"),
        handler=handler,
    )


# ---------------------------------------------------------------------------
# SubprocessDelegator
# ---------------------------------------------------------------------------


class SubprocessDelegator:
    """Delegate to a handler running in a separate process.

    The delegator spawns a command, writes JSON on stdin containing the
    message and serialised context, and parses the handler's stdout as
    an ``AgentResult``.

    This is designed for cross-language delegation (e.g. Python orchestrator
    invoking a Node.js agent) where in-process invocation is not possible.

    Example::

        delegator = SubprocessDelegator("node", "agents/search.js")
        result = await delegator.invoke("search", agent_input, child_ctx)
    """

    def __init__(
        self,
        command: str,
        script_path: str,
        *,
        timeout_seconds: float = DEFAULT_DELEGATOR_TIMEOUT_SECONDS,
    ) -> None:
        """Create a subprocess delegator.

        Args:
            command: Executable to spawn (e.g. ``"node"``, ``"python3"``).
            script_path: Path to the handler script, passed as the first argument.
            timeout_seconds: Maximum wall-clock seconds for the subprocess.
        """
        self._command = command
        self._script_path = script_path
        self._timeout_seconds = timeout_seconds

    async def invoke(
        self,
        handler: str,
        agent_input: AgentInput,
        ctx: ExecContext,
    ) -> AgentResult:
        """Run the handler as a subprocess with context serialisation.

        Writes a JSON payload to stdin containing the ``AgentInput`` fields
        and the serialised ``ExecContext``. Reads stdout and parses it as
        an ``AgentResult``.

        Args:
            handler: Handler name (used for result attribution).
            agent_input: Structured input for the handler.
            ctx: Execution context (serialised and sent to the subprocess).

        Returns:
            ``AgentResult`` parsed from stdout, or an error result on failure.
        """
        payload = self._build_payload(agent_input, ctx)
        stdout, returncode = await self._spawn(payload, ctx)

        if returncode == -1:
            return AgentResult(
                status=AgentStatus.TIMEOUT,
                handler=handler,
                error=f"subprocess timed out after {self._timeout_seconds}s",
            )

        if returncode != 0 and not stdout.strip():
            return AgentResult(
                status=AgentStatus.ERROR,
                handler=handler,
                error=f"subprocess exited with code {returncode}",
            )

        return _parse_result(stdout, handler)

    def _build_payload(
        self, agent_input: AgentInput, ctx: ExecContext
    ) -> str:
        """Serialize the input and context into a JSON string for stdin.

        Args:
            agent_input: The agent input to include.
            ctx: The execution context to serialize.

        Returns:
            JSON string ready to write to the subprocess's stdin.
        """
        return json.dumps({
            "input": asdict(agent_input),
            "context": _serialize_context(ctx),
        })

    async def _spawn(
        self, payload: str, ctx: ExecContext
    ) -> tuple[str, int]:
        """Spawn the subprocess and collect its output.

        Args:
            payload: JSON string to write to stdin.
            ctx: Execution context for event recording.

        Returns:
            A ``(stdout, returncode)`` tuple. ``returncode`` is ``-1`` on timeout.
        """
        ctx.add_event("delegator.start", command=self._command, script=self._script_path)

        process = await asyncio.create_subprocess_exec(
            self._command,
            self._script_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                process.communicate(input=payload.encode()),
                timeout=self._timeout_seconds,
            )
            returncode = process.returncode if process.returncode is not None else -1
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            ctx.add_event("delegator.timeout", command=self._command)
            return "", -1

        ctx.add_event(
            "delegator.end",
            command=self._command,
            returncode=str(returncode),
        )
        return stdout_bytes.decode(errors="replace"), returncode
