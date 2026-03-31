"""In-process runtime — execute async Python functions as handlers (N-611).

Registers and invokes handler functions directly in the event loop,
with circuit breaker protection, timeout enforcement via asyncio.wait_for,
and streaming support for generator-based handlers.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.runtime.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = [
    "InProcessRuntime",
    "InProcessConfig",
    "HandlerFunc",
]

_log = logging.getLogger(__name__)

# -- Constants ---------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS: float = 30.0
"""Maximum wall-clock seconds a handler may run."""


# -- Types -------------------------------------------------------------------

HandlerFunc = Callable[..., object]
"""A handler function: async callable accepting (AgentInput, ExecContext)."""


# -- Config ------------------------------------------------------------------


@dataclass(frozen=True)
class InProcessConfig:
    """Configuration for the in-process runtime.

    Attributes:
        timeout_seconds: Max execution time per handler invocation.
        circuit_breaker: Circuit breaker thresholds applied per handler.
    """

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    circuit_breaker: CircuitBreakerConfig | None = None


# -- Internal registration record -------------------------------------------


@dataclass(frozen=True)
class _RegisteredHandler:
    """A handler function registered with the runtime.

    Attributes:
        name: Unique handler identifier.
        func: The async callable.
        is_async_gen: Whether the function is an async generator (streaming).
    """

    name: str
    func: Callable[..., object]
    is_async_gen: bool


# -- Runtime -----------------------------------------------------------------


class InProcessRuntime:
    """Execute handlers as in-process async function calls.

    Provides per-handler circuit breakers, timeout enforcement via
    asyncio.wait_for, and streaming for async generator handlers.

    Args:
        config: Runtime configuration. Uses defaults when None.
    """

    def __init__(self, config: InProcessConfig | None = None) -> None:
        self._config = config or InProcessConfig()
        self._handlers: dict[str, _RegisteredHandler] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

    # -- Registration --------------------------------------------------------

    def register(self, name: str, func: Callable[..., object]) -> None:
        """Register an async function as a handler.

        Args:
            name: Unique handler identifier.
            func: Async callable accepting (AgentInput, ExecContext).

        Raises:
            ValueError: If *name* is empty or already registered.
            TypeError: If *func* is not an async function or async generator.
        """
        if not name:
            raise ValueError("Handler name must not be empty")
        if name in self._handlers:
            raise ValueError(f"Handler '{name}' is already registered")
        if not inspect.iscoroutinefunction(func) and not inspect.isasyncgenfunction(func):
            raise TypeError(f"Handler '{name}' must be async (coroutine or async generator)")

        self._handlers[name] = _RegisteredHandler(
            name=name,
            func=func,
            is_async_gen=inspect.isasyncgenfunction(func),
        )

    # -- Public API ----------------------------------------------------------

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run a single handler in-process.

        Args:
            handler: Handler name to invoke.
            input: Structured input for the handler.
            ctx: Execution context for tracing, streaming, and cancellation.

        Returns:
            AgentResult with status and output.
        """
        registered = self._handlers.get(handler)
        if registered is None:
            return _not_found_result(handler)

        breaker = self._get_breaker(handler)
        if not breaker.is_allowed():
            return _circuit_open_result(handler)

        ctx.add_event("inprocess.start", handler=handler)
        started_at = time.monotonic()

        result = await self._execute_with_timeout(registered, input, ctx)

        elapsed = time.monotonic() - started_at
        ctx.add_event(
            "inprocess.end",
            handler=handler,
            status=result.status.value,
            elapsed_seconds=f"{elapsed:.3f}",
        )

        _record_on_breaker(breaker, result.status)
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
            AgentResult from the last successfully executed handler.

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

        Creates a child ExecContext inheriting the parent's trace and permissions.

        Args:
            handler: Handler name to delegate to.
            input: Input for the delegated handler.
            parent_ctx: Parent's execution context.

        Returns:
            AgentResult from the delegated handler.
        """
        child_ctx = parent_ctx.child(handler)
        return await self.invoke(handler, input, child_ctx)

    # -- Private: execution --------------------------------------------------

    async def _execute_with_timeout(
        self,
        registered: _RegisteredHandler,
        input: AgentInput,
        ctx: ExecContext,
    ) -> AgentResult:
        """Execute a handler with timeout enforcement.

        Args:
            registered: The registered handler record.
            input: Structured input for the handler.
            ctx: Execution context.

        Returns:
            AgentResult from the handler, or a timeout/error result.
        """
        try:
            if registered.is_async_gen:
                return await asyncio.wait_for(
                    self._execute_streaming(registered, input, ctx),
                    timeout=self._config.timeout_seconds,
                )
            return await asyncio.wait_for(
                self._execute_direct(registered, input, ctx),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return _timeout_result(registered.name, self._config.timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            _log.error("Handler '%s' raised: %s", registered.name, exc)
            return _error_result(registered.name, exc)

    async def _execute_direct(
        self,
        registered: _RegisteredHandler,
        input: AgentInput,
        ctx: ExecContext,
    ) -> AgentResult:
        """Execute a regular async handler and return its result.

        Args:
            registered: The registered handler record.
            input: Structured input.
            ctx: Execution context.

        Returns:
            AgentResult with the handler's output.
        """
        raw_output = await registered.func(input, ctx)
        return AgentResult(
            status=AgentStatus.SUCCESS,
            output=str(raw_output) if raw_output is not None else "",
            handler=registered.name,
        )

    async def _execute_streaming(
        self,
        registered: _RegisteredHandler,
        input: AgentInput,
        ctx: ExecContext,
    ) -> AgentResult:
        """Execute a streaming (async generator) handler, pushing chunks to ctx.stream.

        Args:
            registered: The registered handler record.
            input: Structured input.
            ctx: Execution context with optional stream sink.

        Returns:
            AgentResult with the concatenated output.
        """
        collected: list[str] = []
        async for chunk in registered.func(input, ctx):
            text = str(chunk)
            collected.append(text)
            if ctx.stream is not None:
                await ctx.stream.push(text)

        return AgentResult(
            status=AgentStatus.SUCCESS,
            output="".join(collected),
            handler=registered.name,
        )

    # -- Circuit breaker helpers ---------------------------------------------

    def _get_breaker(self, handler: str) -> CircuitBreaker:
        """Return the circuit breaker for *handler*, creating one if needed.

        Args:
            handler: Handler name used as the breaker key.

        Returns:
            The CircuitBreaker instance for this handler.
        """
        if handler not in self._breakers:
            self._breakers[handler] = CircuitBreaker(self._config.circuit_breaker)
        return self._breakers[handler]


# -- Pure helpers ------------------------------------------------------------


def _record_on_breaker(breaker: CircuitBreaker, status: AgentStatus) -> None:
    """Record success or failure on the circuit breaker.

    Args:
        breaker: The handler's circuit breaker.
        status: The outcome status of the invocation.
    """
    if status == AgentStatus.SUCCESS:
        breaker.record_success()
    else:
        breaker.record_failure()


def _not_found_result(handler: str) -> AgentResult:
    """Build an error result for an unregistered handler.

    Args:
        handler: The handler name that was not found.

    Returns:
        AgentResult with ERROR status.
    """
    return AgentResult(
        status=AgentStatus.ERROR,
        handler=handler,
        error=f"handler '{handler}' is not registered",
    )


def _circuit_open_result(handler: str) -> AgentResult:
    """Build an error result for a handler whose circuit is open.

    Args:
        handler: The handler that was rejected.

    Returns:
        AgentResult with ERROR status.
    """
    return AgentResult(
        status=AgentStatus.ERROR,
        handler=handler,
        error=f"circuit open for handler '{handler}'",
    )


def _timeout_result(handler: str, timeout_seconds: float) -> AgentResult:
    """Build a TIMEOUT result.

    Args:
        handler: Handler name.
        timeout_seconds: The timeout limit that was exceeded.

    Returns:
        AgentResult with TIMEOUT status.
    """
    return AgentResult(
        status=AgentStatus.TIMEOUT,
        handler=handler,
        error=f"handler '{handler}' timed out after {timeout_seconds}s",
    )


def _error_result(handler: str, exc: Exception) -> AgentResult:
    """Build an ERROR result from an exception.

    Args:
        handler: Handler name.
        exc: The exception that was raised.

    Returns:
        AgentResult with ERROR status.
    """
    return AgentResult(
        status=AgentStatus.ERROR,
        handler=handler,
        error=f"{type(exc).__name__}: {exc}",
    )
