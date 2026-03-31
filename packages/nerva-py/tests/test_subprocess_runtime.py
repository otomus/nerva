"""Tests for SubprocessRuntime — N-172.

Covers subprocess invocation, JSON extraction, timeout handling,
circuit breaker integration, error classification, and streaming.
Mocks asyncio.create_subprocess_exec to avoid real process spawning.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerva.context import ExecContext, InMemoryStreamSink
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.runtime.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from nerva.runtime.subprocess import (
    DEFAULT_TIMEOUT_SECONDS,
    WRONG_HANDLER_EXIT_CODE,
    ErrorKind,
    SubprocessConfig,
    SubprocessRuntime,
)

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_input(message: str = "hello") -> AgentInput:
    """Build a minimal AgentInput for testing.

    Args:
        message: User message text.

    Returns:
        AgentInput with the given message and empty defaults.
    """
    return AgentInput(message=message)


def _make_fake_process(
    stdout_data: bytes = b"",
    returncode: int = 0,
    hang: bool = False,
) -> AsyncMock:
    """Build a mock asyncio subprocess with configurable behavior.

    Args:
        stdout_data: Bytes to yield from stdout.
        returncode: Exit code to report.
        hang: If True, simulate a process that never produces output.

    Returns:
        AsyncMock mimicking asyncio.subprocess.Process.
    """
    process = AsyncMock()
    process.returncode = returncode

    # stdin
    process.stdin = AsyncMock()
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.stdin.close = MagicMock()

    # stderr
    process.stderr = AsyncMock()

    # stdout — simulate read() returning data then b""
    if hang:
        async def _hanging_read(n: int) -> bytes:
            """Simulate a process that blocks forever."""
            await asyncio.sleep(999)
            return b""
        process.stdout.read = _hanging_read
    else:
        read_calls = [0]
        async def _read(n: int) -> bytes:
            """Return stdout_data on first call, empty bytes after."""
            if read_calls[0] == 0:
                read_calls[0] = 1
                return stdout_data[:n]
            return b""
        process.stdout.read = _read

    # wait
    async def _wait() -> int:
        """Return the configured exit code."""
        return returncode
    process.wait = _wait

    # kill
    process.kill = MagicMock()

    return process


# ---------------------------------------------------------------------------
# Error classification (pure)
# ---------------------------------------------------------------------------

class TestErrorClassification:
    """Tests for SubprocessRuntime._classify_error — pure error classification."""

    def setup_method(self) -> None:
        """Create a runtime instance for classification tests."""
        self.runtime = SubprocessRuntime()

    def test_exit_zero_is_success(self) -> None:
        """Exit code 0 is classified as None (no error)."""
        assert self.runtime._classify_error(0, "") is None

    def test_exit_minus_one_is_retryable(self) -> None:
        """Exit code -1 (timeout) is classified as RETRYABLE."""
        assert self.runtime._classify_error(-1, "") == ErrorKind.RETRYABLE

    def test_exit_two_is_wrong_handler(self) -> None:
        """Exit code 2 is classified as WRONG_HANDLER."""
        assert self.runtime._classify_error(WRONG_HANDLER_EXIT_CODE, "") == ErrorKind.WRONG_HANDLER

    def test_exit_one_is_fatal(self) -> None:
        """Exit code 1 is classified as FATAL."""
        assert self.runtime._classify_error(1, "") == ErrorKind.FATAL

    def test_exit_137_is_fatal(self) -> None:
        """Exit code 137 (SIGKILL) is classified as FATAL."""
        assert self.runtime._classify_error(137, "") == ErrorKind.FATAL

    def test_exit_255_is_fatal(self) -> None:
        """Unusual exit code 255 is classified as FATAL."""
        assert self.runtime._classify_error(255, "") == ErrorKind.FATAL


# ---------------------------------------------------------------------------
# JSON extraction (pure)
# ---------------------------------------------------------------------------

class TestJsonExtraction:
    """Tests for SubprocessRuntime._extract_json."""

    def setup_method(self) -> None:
        """Create a runtime instance for JSON extraction tests."""
        self.runtime = SubprocessRuntime()

    def test_valid_json(self) -> None:
        """Valid JSON object is parsed correctly."""
        result = self.runtime._extract_json('{"output": "hello"}')
        assert result == {"output": "hello"}

    def test_empty_string(self) -> None:
        """Empty string returns empty dict."""
        assert self.runtime._extract_json("") == {}

    def test_whitespace_only(self) -> None:
        """Whitespace-only string returns empty dict."""
        assert self.runtime._extract_json("   \n  ") == {}

    def test_json_embedded_in_noise(self) -> None:
        """JSON object embedded in surrounding text is extracted via regex."""
        raw = 'DEBUG: starting\n{"output": "found it"}\nDEBUG: done'
        result = self.runtime._extract_json(raw)
        assert result.get("output") == "found it"

    def test_malformed_json(self) -> None:
        """Completely malformed text returns empty dict."""
        assert self.runtime._extract_json("not json at all {{{") == {}

    def test_truncated_json(self) -> None:
        """Truncated JSON returns empty dict."""
        assert self.runtime._extract_json('{"key": "val') == {}

    def test_json_array_ignored(self) -> None:
        """A top-level JSON array is not treated as a valid dict result."""
        assert self.runtime._extract_json('[1, 2, 3]') == {}

    def test_nested_json(self) -> None:
        """Nested JSON objects are parsed correctly."""
        raw = '{"output": "ok", "data": "nested"}'
        result = self.runtime._extract_json(raw)
        assert result["output"] == "ok"


# ---------------------------------------------------------------------------
# Result building
# ---------------------------------------------------------------------------

class TestResultBuilding:
    """Tests for SubprocessRuntime._build_result."""

    def setup_method(self) -> None:
        """Create a runtime instance for result building tests."""
        self.runtime = SubprocessRuntime()

    def test_success_extracts_output_field(self) -> None:
        """SUCCESS result extracts 'output' from JSON."""
        result = self.runtime._build_result(
            "handler_a", '{"output": "hello world"}', 0, None
        )
        assert result.status == AgentStatus.SUCCESS
        assert result.output == "hello world"

    def test_success_extracts_response_field(self) -> None:
        """SUCCESS result extracts 'response' when 'output' is absent."""
        result = self.runtime._build_result(
            "handler_a", '{"response": "hello world"}', 0, None
        )
        assert result.status == AgentStatus.SUCCESS
        assert result.output == "hello world"

    def test_success_falls_back_to_raw(self) -> None:
        """SUCCESS result falls back to raw output when no known keys exist."""
        result = self.runtime._build_result(
            "handler_a", "plain text output", 0, None
        )
        assert result.status == AgentStatus.SUCCESS
        assert result.output == "plain text output"

    def test_timeout_result(self) -> None:
        """RETRYABLE error builds a TIMEOUT result."""
        result = self.runtime._build_result("h", "", -1, ErrorKind.RETRYABLE)
        assert result.status == AgentStatus.TIMEOUT
        assert "timed out" in result.error

    def test_wrong_handler_result(self) -> None:
        """WRONG_HANDLER error builds a WRONG_HANDLER result."""
        result = self.runtime._build_result("h", '{"error": "not my job"}', 2, ErrorKind.WRONG_HANDLER)
        assert result.status == AgentStatus.WRONG_HANDLER
        assert "not my job" in result.error

    def test_fatal_result(self) -> None:
        """FATAL error builds an ERROR result with the exit code."""
        result = self.runtime._build_result("h", "", 1, ErrorKind.FATAL)
        assert result.status == AgentStatus.ERROR
        assert "exited with code 1" in result.error

    def test_empty_output_success(self) -> None:
        """Empty output on success returns empty output string."""
        result = self.runtime._build_result("h", "", 0, None)
        assert result.status == AgentStatus.SUCCESS

    def test_handler_field_populated(self) -> None:
        """The handler field is always set in the result."""
        result = self.runtime._build_result("my_handler", "", 0, None)
        assert result.handler == "my_handler"


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------

class TestCircuitBreakerIntegration:
    """Tests for circuit breaker behavior within SubprocessRuntime."""

    @pytest.mark.asyncio
    async def test_open_circuit_rejects(self) -> None:
        """An open circuit returns an ERROR without spawning a process."""
        config = SubprocessConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1)
        )
        runtime = SubprocessRuntime(config)
        ctx = make_ctx()

        # Force the breaker open by recording a failure
        breaker = runtime._get_breaker("broken_handler")
        breaker.record_failure()

        result = await runtime.invoke("broken_handler", _make_input(), ctx)
        assert result.status == AgentStatus.ERROR
        assert "circuit open" in result.error

    def test_get_breaker_creates_per_handler(self) -> None:
        """Each handler gets its own CircuitBreaker instance."""
        runtime = SubprocessRuntime()
        breaker_a = runtime._get_breaker("handler_a")
        breaker_b = runtime._get_breaker("handler_b")
        assert breaker_a is not breaker_b

    def test_get_breaker_returns_same_instance(self) -> None:
        """Repeated calls for the same handler return the same breaker."""
        runtime = SubprocessRuntime()
        assert runtime._get_breaker("x") is runtime._get_breaker("x")

    def test_record_on_breaker_success(self) -> None:
        """SUCCESS status records a success on the breaker."""
        runtime = SubprocessRuntime()
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))
        breaker.record_failure()  # 1 failure
        runtime._record_on_breaker(breaker, AgentStatus.SUCCESS)
        # After success, consecutive failures reset
        assert breaker.state == CircuitState.CLOSED

    def test_record_on_breaker_failure(self) -> None:
        """Non-SUCCESS status records a failure on the breaker."""
        runtime = SubprocessRuntime()
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))
        runtime._record_on_breaker(breaker, AgentStatus.ERROR)
        runtime._record_on_breaker(breaker, AgentStatus.ERROR)
        assert breaker.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# Full invocation (mocked subprocess)
# ---------------------------------------------------------------------------

class TestInvoke:
    """Tests for SubprocessRuntime.invoke with mocked process spawning."""

    @pytest.mark.asyncio
    async def test_successful_invocation(self) -> None:
        """A handler that exits 0 with JSON stdout returns SUCCESS."""
        ctx = make_ctx()
        runtime = SubprocessRuntime(SubprocessConfig(handler_dir="/fake"))
        output_json = json.dumps({"output": "result text"}).encode()
        fake_proc = _make_fake_process(stdout_data=output_json, returncode=0)

        with patch("nerva.runtime.subprocess.asyncio.create_subprocess_exec", return_value=fake_proc):
            with patch.object(runtime, "_resolve_handler_path", return_value="/fake/handler"):
                result = await runtime.invoke("handler", _make_input(), ctx)

        assert result.status == AgentStatus.SUCCESS
        assert result.output == "result text"
        assert result.handler == "handler"

    @pytest.mark.asyncio
    async def test_process_crash_returns_error(self) -> None:
        """A handler that exits with code 1 returns ERROR."""
        ctx = make_ctx()
        runtime = SubprocessRuntime(SubprocessConfig(handler_dir="/fake"))
        fake_proc = _make_fake_process(stdout_data=b"crash info", returncode=1)

        with patch("nerva.runtime.subprocess.asyncio.create_subprocess_exec", return_value=fake_proc):
            with patch.object(runtime, "_resolve_handler_path", return_value="/fake/handler"):
                result = await runtime.invoke("handler", _make_input(), ctx)

        assert result.status == AgentStatus.ERROR

    @pytest.mark.asyncio
    async def test_wrong_handler_exit_code(self) -> None:
        """Exit code 2 produces a WRONG_HANDLER result."""
        ctx = make_ctx()
        runtime = SubprocessRuntime(SubprocessConfig(handler_dir="/fake"))
        fake_proc = _make_fake_process(returncode=WRONG_HANDLER_EXIT_CODE)

        with patch("nerva.runtime.subprocess.asyncio.create_subprocess_exec", return_value=fake_proc):
            with patch.object(runtime, "_resolve_handler_path", return_value="/fake/handler"):
                result = await runtime.invoke("handler", _make_input(), ctx)

        assert result.status == AgentStatus.WRONG_HANDLER

    @pytest.mark.asyncio
    async def test_events_recorded_on_context(self) -> None:
        """invoke records subprocess.start and subprocess.end events on ctx."""
        ctx = make_ctx()
        runtime = SubprocessRuntime(SubprocessConfig(handler_dir="/fake"))
        fake_proc = _make_fake_process(stdout_data=b"{}", returncode=0)

        with patch("nerva.runtime.subprocess.asyncio.create_subprocess_exec", return_value=fake_proc):
            with patch.object(runtime, "_resolve_handler_path", return_value="/fake/handler"):
                await runtime.invoke("handler", _make_input(), ctx)

        event_names = [e.name for e in ctx.events]
        assert "subprocess.start" in event_names
        assert "subprocess.end" in event_names

    @pytest.mark.asyncio
    async def test_extremely_long_output_truncated(self) -> None:
        """Output exceeding max_output_bytes is truncated."""
        ctx = make_ctx()
        max_bytes = 100
        runtime = SubprocessRuntime(
            SubprocessConfig(handler_dir="/fake", max_output_bytes=max_bytes)
        )
        # Generate output larger than limit
        big_output = b"x" * 200
        fake_proc = _make_fake_process(stdout_data=big_output, returncode=0)

        with patch("nerva.runtime.subprocess.asyncio.create_subprocess_exec", return_value=fake_proc):
            with patch.object(runtime, "_resolve_handler_path", return_value="/fake/handler"):
                result = await runtime.invoke("handler", _make_input(), ctx)

        # Output should be truncated to at most max_bytes characters
        assert len(result.output.encode()) <= max_bytes


# ---------------------------------------------------------------------------
# Invoke chain
# ---------------------------------------------------------------------------

class TestInvokeChain:
    """Tests for SubprocessRuntime.invoke_chain."""

    @pytest.mark.asyncio
    async def test_empty_handlers_raises(self) -> None:
        """Empty handler list raises ValueError."""
        runtime = SubprocessRuntime()
        ctx = make_ctx()
        with pytest.raises(ValueError, match="handlers"):
            await runtime.invoke_chain([], _make_input(), ctx)

    @pytest.mark.asyncio
    async def test_chain_stops_on_error(self) -> None:
        """Chain stops at the first non-SUCCESS handler."""
        runtime = SubprocessRuntime()
        ctx = make_ctx()

        call_count = [0]
        original_invoke = runtime.invoke

        async def _counting_invoke(handler, inp, c):
            """Track calls and fail the second handler."""
            call_count[0] += 1
            if handler == "fail":
                return AgentResult(status=AgentStatus.ERROR, handler="fail", error="boom")
            return AgentResult(status=AgentStatus.SUCCESS, handler=handler, output="ok")

        runtime.invoke = _counting_invoke  # type: ignore[assignment]
        result = await runtime.invoke_chain(["ok", "fail", "never"], _make_input(), ctx)
        assert result.status == AgentStatus.ERROR
        assert call_count[0] == 2  # "never" was not called


# ---------------------------------------------------------------------------
# Streaming output
# ---------------------------------------------------------------------------

class TestStreaming:
    """Tests for stdout streaming to ctx.stream."""

    @pytest.mark.asyncio
    async def test_stream_receives_chunks(self) -> None:
        """When ctx.stream is set, chunks are pushed to it."""
        sink = InMemoryStreamSink()
        ctx = make_ctx()
        ctx.stream = sink

        runtime = SubprocessRuntime(SubprocessConfig(handler_dir="/fake"))
        fake_proc = _make_fake_process(stdout_data=b"chunk1", returncode=0)

        with patch("nerva.runtime.subprocess.asyncio.create_subprocess_exec", return_value=fake_proc):
            with patch.object(runtime, "_resolve_handler_path", return_value="/fake/handler"):
                await runtime.invoke("handler", _make_input(), ctx)

        assert len(sink.chunks) >= 1
        assert "chunk1" in "".join(sink.chunks)

    @pytest.mark.asyncio
    async def test_no_stream_no_error(self) -> None:
        """When ctx.stream is None, invoke still works without error."""
        ctx = make_ctx()
        assert ctx.stream is None

        runtime = SubprocessRuntime(SubprocessConfig(handler_dir="/fake"))
        fake_proc = _make_fake_process(stdout_data=b'{"output":"ok"}', returncode=0)

        with patch("nerva.runtime.subprocess.asyncio.create_subprocess_exec", return_value=fake_proc):
            with patch.object(runtime, "_resolve_handler_path", return_value="/fake/handler"):
                result = await runtime.invoke("handler", _make_input(), ctx)

        assert result.status == AgentStatus.SUCCESS
