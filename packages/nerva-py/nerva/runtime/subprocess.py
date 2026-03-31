"""Subprocess runtime — execute agent handlers as child processes.

Each handler is a script or binary that:
1. Receives ``AgentInput`` as JSON on stdin
2. Writes ``AgentResult``-shaped JSON to stdout
3. Exits with 0 on success, non-zero on failure

Tasks: N-121 (spawn + collect), N-123 (circuit breaker), N-124 (JSON extraction),
N-125 (error classification), N-126 (streaming).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from nerva.context import ExecContext
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.runtime.circuit_breaker import CircuitBreaker, CircuitBreakerConfig


# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Maximum wall-clock seconds a handler process may run."""

MAX_OUTPUT_BYTES = 1_048_576  # 1 MB
"""Maximum stdout bytes collected from a handler before truncation."""

STREAM_CHUNK_SIZE = 4096
"""Bytes read per iteration when streaming handler output."""

WRONG_HANDLER_EXIT_CODE = 2
"""Exit code that signals the handler cannot handle this input."""

JSON_EXTRACT_PATTERN = re.compile(r"\{[^{}]*\}|\{.*\}", re.DOTALL)
"""Regex to locate a JSON object in noisy handler output."""


# ---------------------------------------------------------------------------
# ErrorKind (N-125)
# ---------------------------------------------------------------------------


class ErrorKind(StrEnum):
    """Classification of handler errors.

    Members:
        RETRYABLE: Transient failure — safe to retry (e.g. timeout).
        FATAL: Permanent failure — retrying will not help.
        WRONG_HANDLER: Handler cannot handle the given input.
    """

    RETRYABLE = "retryable"
    FATAL = "fatal"
    WRONG_HANDLER = "wrong_handler"


# ---------------------------------------------------------------------------
# SubprocessConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubprocessConfig:
    """Configuration for the subprocess runtime.

    Attributes:
        timeout_seconds: Max execution time per handler invocation.
        circuit_breaker: Circuit breaker thresholds applied per handler.
        max_output_bytes: Max stdout bytes to collect before truncation.
        handler_dir: Base directory for resolving handler scripts.
    """

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    circuit_breaker: CircuitBreakerConfig | None = None
    max_output_bytes: int = MAX_OUTPUT_BYTES
    handler_dir: str = "."


# ---------------------------------------------------------------------------
# SubprocessRuntime (N-121)
# ---------------------------------------------------------------------------


class SubprocessRuntime:
    """Execute handlers as child processes with lifecycle management.

    Provides per-handler circuit breakers (N-123), timeout enforcement,
    structured JSON extraction from output (N-124), error classification
    (N-125), and incremental streaming to ``ctx.stream`` (N-126).

    Example::

        runtime = SubprocessRuntime(SubprocessConfig(handler_dir="/opt/handlers"))
        result = await runtime.invoke("search", agent_input, ctx)
    """

    def __init__(self, config: SubprocessConfig | None = None) -> None:
        """Create a new subprocess runtime.

        Args:
            config: Runtime configuration. Uses defaults when ``None``.
        """
        self._config = config or SubprocessConfig()
        self._breakers: dict[str, CircuitBreaker] = {}

    # -- Public API --------------------------------------------------------

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run a single handler as a subprocess.

        Args:
            handler: Handler name, resolved relative to ``handler_dir``.
            input: Structured input serialised to JSON on stdin.
            ctx: Execution context for tracing, streaming, and cancellation.

        Returns:
            ``AgentResult`` populated from the handler's stdout JSON,
            or an error result if the handler fails or times out.
        """
        breaker = self._get_breaker(handler)
        if not breaker.is_allowed():
            return self._circuit_open_result(handler)

        ctx.add_event("subprocess.start", handler=handler)
        started_at = time.monotonic()

        input_json = json.dumps(asdict(input))
        stdout, returncode = await self._spawn_process(handler, input_json, ctx)

        elapsed = time.monotonic() - started_at
        ctx.add_event(
            "subprocess.end",
            handler=handler,
            returncode=str(returncode),
            elapsed_seconds=f"{elapsed:.3f}",
        )

        error_kind = self._classify_error(returncode, stdout)
        result = self._build_result(handler, stdout, returncode, error_kind)

        self._record_on_breaker(breaker, result.status)
        return result

    async def invoke_chain(
        self, handlers: list[str], input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run handlers in sequence, piping each output as the next input's message.

        Stops early if any handler returns a non-SUCCESS status.

        Args:
            handlers: Ordered list of handler names.
            input: Initial input for the first handler.
            ctx: Execution context shared across the chain.

        Returns:
            ``AgentResult`` from the last successfully executed handler,
            or the first non-SUCCESS result.

        Raises:
            ValueError: If *handlers* is empty.
        """
        if not handlers:
            raise ValueError("handlers list must not be empty")

        current_input = input
        result = AgentResult(status=AgentStatus.ERROR, handler="", error="no handlers ran")

        for handler_name in handlers:
            result = await self.invoke(handler_name, current_input, ctx)
            if result.status != AgentStatus.SUCCESS:
                return result
            current_input = AgentInput(
                message=result.output,
                args=current_input.args,
                tools=current_input.tools,
                history=current_input.history,
            )

        return result

    async def delegate(
        self, handler: str, input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Invoke a handler from within another handler (agent-to-agent delegation).

        Creates a child ``ExecContext`` inheriting the parent's trace, permissions,
        and stream, then runs the handler in that child context.

        Args:
            handler: Handler name to delegate to.
            input: Input for the delegated handler.
            parent_ctx: Parent's execution context.

        Returns:
            ``AgentResult`` from the delegated handler.
        """
        child_ctx = parent_ctx.child(handler)
        return await self.invoke(handler, input, child_ctx)

    # -- Circuit breaker helpers -------------------------------------------

    def _get_breaker(self, handler: str) -> CircuitBreaker:
        """Return the circuit breaker for *handler*, creating one if needed.

        Args:
            handler: Handler name used as the breaker key.

        Returns:
            The ``CircuitBreaker`` instance for this handler.
        """
        if handler not in self._breakers:
            self._breakers[handler] = CircuitBreaker(self._config.circuit_breaker)
        return self._breakers[handler]

    def _circuit_open_result(self, handler: str) -> AgentResult:
        """Build an error result for a handler whose circuit is open.

        Args:
            handler: The handler that was rejected.

        Returns:
            ``AgentResult`` with ERROR status and descriptive error message.
        """
        return AgentResult(
            status=AgentStatus.ERROR,
            handler=handler,
            error=f"circuit open for handler '{handler}'",
        )

    def _record_on_breaker(self, breaker: CircuitBreaker, status: AgentStatus) -> None:
        """Record success or failure on the circuit breaker.

        Args:
            breaker: The handler's circuit breaker.
            status: The outcome status of the invocation.
        """
        if status == AgentStatus.SUCCESS:
            breaker.record_success()
        else:
            breaker.record_failure()

    # -- Process spawning (N-121 + N-126) ----------------------------------

    async def _spawn_process(
        self, handler: str, input_json: str, ctx: ExecContext
    ) -> tuple[str, int]:
        """Spawn a subprocess, collect stdout, and enforce timeout.

        When ``ctx.stream`` is set, pushes stdout chunks incrementally (N-126).

        Args:
            handler: Handler name resolved to a filesystem path.
            input_json: Serialised ``AgentInput`` written to stdin.
            ctx: Execution context carrying stream sink and cancellation.

        Returns:
            A ``(stdout, returncode)`` tuple. ``returncode`` is ``-1`` on timeout.
        """
        command_path = self._resolve_handler_path(handler)

        process = await asyncio.create_subprocess_exec(
            command_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout = await self._collect_output(process, input_json, ctx)
            returncode = process.returncode if process.returncode is not None else -1
        except asyncio.TimeoutError:
            await self._kill_process(process)
            ctx.add_event("subprocess.timeout", handler=handler)
            return "", -1

        return stdout, returncode

    def _resolve_handler_path(self, handler: str) -> str:
        """Resolve a handler name to an executable filesystem path.

        Checks the configured ``handler_dir`` first. If no file exists there,
        returns the bare handler name for PATH-based resolution.

        Args:
            handler: Handler name (e.g. ``"search"``).

        Returns:
            Absolute path if found in ``handler_dir``, otherwise the bare name.
        """
        candidate = Path(self._config.handler_dir) / handler
        if candidate.is_file():
            return str(candidate.resolve())
        return handler

    async def _collect_output(
        self, process: asyncio.subprocess.Process, input_json: str, ctx: ExecContext
    ) -> str:
        """Feed stdin, collect stdout up to the byte limit, and stream chunks.

        Args:
            process: The running subprocess.
            input_json: JSON payload to write to stdin.
            ctx: Execution context with optional stream sink.

        Returns:
            The collected stdout as a string, truncated to ``max_output_bytes``.

        Raises:
            asyncio.TimeoutError: If the process exceeds ``timeout_seconds``.
        """
        if process.stdin is not None:
            process.stdin.write(input_json.encode())
            await process.stdin.drain()
            process.stdin.close()

        stdout_bytes = await self._read_stdout_with_streaming(process, ctx)
        await asyncio.wait_for(process.wait(), timeout=self._config.timeout_seconds)

        return stdout_bytes.decode(errors="replace")

    async def _read_stdout_with_streaming(
        self, process: asyncio.subprocess.Process, ctx: ExecContext
    ) -> bytes:
        """Read stdout in chunks, pushing to the stream sink when available.

        Args:
            process: The running subprocess with a stdout pipe.
            ctx: Execution context with optional ``stream`` sink.

        Returns:
            All collected stdout bytes, truncated to ``max_output_bytes``.
        """
        if process.stdout is None:
            return b""

        collected = bytearray()
        bytes_remaining = self._config.max_output_bytes

        while bytes_remaining > 0:
            chunk = await asyncio.wait_for(
                process.stdout.read(min(STREAM_CHUNK_SIZE, bytes_remaining)),
                timeout=self._config.timeout_seconds,
            )
            if not chunk:
                break

            collected.extend(chunk)
            bytes_remaining -= len(chunk)

            if ctx.stream is not None:
                await ctx.stream.push(chunk.decode(errors="replace"))

        return bytes(collected)

    async def _kill_process(self, process: asyncio.subprocess.Process) -> None:
        """Terminate a subprocess and wait for it to exit.

        Args:
            process: The subprocess to kill.
        """
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()

    # -- JSON extraction (N-124) -------------------------------------------

    def _extract_json(self, raw_output: str) -> dict[str, str]:
        """Extract a JSON object from potentially noisy handler output.

        Tries parsing the entire output first. Falls back to regex extraction
        of the first JSON-like object.

        Args:
            raw_output: Raw stdout from the handler process.

        Returns:
            Parsed dict if JSON was found, empty dict otherwise.
        """
        stripped = raw_output.strip()
        if not stripped:
            return {}

        parsed = self._try_parse_json(stripped)
        if parsed is not None:
            return parsed

        return self._regex_extract_json(stripped)

    def _try_parse_json(self, text: str) -> dict[str, str] | None:
        """Attempt to parse the entire text as JSON.

        Args:
            text: Candidate JSON string.

        Returns:
            Parsed dict if successful, ``None`` otherwise.
        """
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    def _regex_extract_json(self, text: str) -> dict[str, str]:
        """Use regex to find and parse the first JSON object in *text*.

        Args:
            text: Noisy output that may contain a JSON object.

        Returns:
            Parsed dict from the first valid JSON match, or empty dict.
        """
        for match in JSON_EXTRACT_PATTERN.finditer(text):
            parsed = self._try_parse_json(match.group())
            if parsed is not None:
                return parsed
        return {}

    # -- Error classification (N-125) --------------------------------------

    def _classify_error(self, returncode: int, output: str) -> ErrorKind | None:
        """Classify a handler's exit status into an error kind.

        Args:
            returncode: Process exit code (``-1`` for timeout).
            output: Collected stdout (unused currently, reserved for future heuristics).

        Returns:
            ``None`` if the handler succeeded, otherwise an ``ErrorKind``.
        """
        if returncode == 0:
            return None
        if returncode == -1:
            return ErrorKind.RETRYABLE
        if returncode == WRONG_HANDLER_EXIT_CODE:
            return ErrorKind.WRONG_HANDLER
        return ErrorKind.FATAL

    # -- Result building ---------------------------------------------------

    def _build_result(
        self,
        handler: str,
        output: str,
        returncode: int,
        error_kind: ErrorKind | None,
    ) -> AgentResult:
        """Construct an ``AgentResult`` from handler output and exit status.

        Args:
            handler: Name of the handler that ran.
            output: Raw stdout collected from the process.
            returncode: Process exit code.
            error_kind: Classified error, or ``None`` on success.

        Returns:
            Fully populated ``AgentResult``.
        """
        if error_kind is None:
            return self._build_success_result(handler, output)
        if error_kind == ErrorKind.RETRYABLE:
            return self._build_timeout_result(handler)
        if error_kind == ErrorKind.WRONG_HANDLER:
            return self._build_wrong_handler_result(handler, output)
        return self._build_fatal_result(handler, output, returncode)

    def _build_success_result(self, handler: str, output: str) -> AgentResult:
        """Build a SUCCESS result, extracting structured data from output.

        Args:
            handler: Handler name.
            output: Raw stdout from the handler.

        Returns:
            ``AgentResult`` with SUCCESS status and extracted data.
        """
        data = self._extract_json(output)
        response_text = data.pop("output", "") or data.pop("response", "") or output
        return AgentResult(
            status=AgentStatus.SUCCESS,
            output=response_text,
            data=data,
            handler=handler,
        )

    def _build_timeout_result(self, handler: str) -> AgentResult:
        """Build a TIMEOUT result.

        Args:
            handler: Handler name.

        Returns:
            ``AgentResult`` with TIMEOUT status.
        """
        return AgentResult(
            status=AgentStatus.TIMEOUT,
            handler=handler,
            error=f"handler '{handler}' timed out after {self._config.timeout_seconds}s",
        )

    def _build_wrong_handler_result(self, handler: str, output: str) -> AgentResult:
        """Build a WRONG_HANDLER result.

        Args:
            handler: Handler name.
            output: Raw stdout (may contain a reason).

        Returns:
            ``AgentResult`` with WRONG_HANDLER status.
        """
        data = self._extract_json(output)
        reason = data.get("error", output.strip() or "handler declined the input")
        return AgentResult(
            status=AgentStatus.WRONG_HANDLER,
            handler=handler,
            error=reason,
            data=data,
        )

    def _build_fatal_result(
        self, handler: str, output: str, returncode: int
    ) -> AgentResult:
        """Build an ERROR result for a fatal (non-retryable) failure.

        Args:
            handler: Handler name.
            output: Raw stdout from the handler.
            returncode: Non-zero exit code.

        Returns:
            ``AgentResult`` with ERROR status.
        """
        data = self._extract_json(output)
        error_msg = data.get("error", f"handler '{handler}' exited with code {returncode}")
        return AgentResult(
            status=AgentStatus.ERROR,
            output=output,
            data=data,
            handler=handler,
            error=error_msg,
        )
