"""Orchestrator — wires all Nerva primitives into a single request handler.

The orchestrator owns the full request lifecycle:
message -> context -> policy -> memory -> router -> runtime -> responder -> response

All primitives are injected — none are created internally. Optional primitives
(tools, memory, registry, policy) gracefully degrade when absent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from enum import StrEnum
from typing import AsyncIterator, Callable, Awaitable, TYPE_CHECKING

from nerva.context import ExecContext, InMemoryStreamSink
from nerva.policy import PolicyAction, PolicyDecision
from nerva.responder import API_CHANNEL, Channel, Response
from nerva.runtime import AgentInput, AgentResult, AgentStatus

if TYPE_CHECKING:
    from nerva.memory import Memory, MemoryEvent, MemoryTier
    from nerva.policy import PolicyEngine
    from nerva.registry import Registry, RegistryPatch
    from nerva.responder import Responder
    from nerva.router import IntentResult, IntentRouter
    from nerva.runtime import AgentRuntime
    from nerva.tools import ToolManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLICY_ACTION_ROUTE = "route"
"""Policy action kind for routing a user message."""

POLICY_ACTION_INVOKE = "invoke_agent"
"""Policy action kind for invoking a handler."""

FALLBACK_HANDLER = "__fallback__"
"""Sentinel handler name used when the router returns no candidates."""

STREAM_POLL_INTERVAL_SECONDS = 0.01
"""Interval between stream sink polls in the stream() generator."""

DEFAULT_MIDDLEWARE_PRIORITY = 100
"""Default priority for middleware registration (lower runs first)."""

DEFAULT_MAX_DELEGATION_DEPTH = 5
"""Maximum delegation depth before returning an error result."""

DELEGATION_DEPTH_EXCEEDED_TEMPLATE = "Delegation depth limit exceeded (max: {n})"
"""Error message template when delegation depth is exceeded."""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PolicyDeniedError(Exception):
    """Raised when policy blocks a request.

    Attributes:
        decision: The denial decision from the policy engine.
    """

    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason or "denied by policy")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class MiddlewareStage(StrEnum):
    """Pipeline stages where middleware can be inserted.

    Members:
        BEFORE_ROUTE: After context creation, before intent classification.
        BEFORE_INVOKE: After routing, before handler execution.
        AFTER_INVOKE: After handler execution, before response formatting.
        BEFORE_RESPOND: After formatting, before returning the response.
    """

    BEFORE_ROUTE = "before_route"
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    BEFORE_RESPOND = "before_respond"


MiddlewareHandler = Callable[[ExecContext, object], Awaitable[object | None]]
"""Middleware signature: ``async (ctx, payload) -> payload | None``.

If the handler returns ``None``, the pipeline continues with the original payload.
If it returns a value, that value replaces the payload for subsequent middleware
and the next pipeline stage.
"""

MiddlewareErrorHandler = Callable[[Exception, MiddlewareStage, ExecContext], Awaitable[None]]
"""Error handler signature: ``async (error, stage, ctx) -> None``.

Called when a middleware handler raises an exception. Receives the exception,
the stage where it occurred, and the current execution context.
"""


# ---------------------------------------------------------------------------
# Middleware entry — handler + priority
# ---------------------------------------------------------------------------


class _MiddlewareEntry:
    """Internal wrapper pairing a handler with its execution priority.

    Lower priority values run first. Entries with equal priority
    preserve registration order.

    Attributes:
        handler: The async middleware callable.
        priority: Execution order (lower = earlier).
        insertion_order: Tie-breaking sequence number.
    """

    __slots__ = ("handler", "priority", "insertion_order")

    def __init__(
        self, handler: MiddlewareHandler, priority: int, insertion_order: int
    ) -> None:
        self.handler = handler
        self.priority = priority
        self.insertion_order = insertion_order

    def sort_key(self) -> tuple[int, int]:
        """Return a tuple suitable for stable sorting by priority.

        Returns:
            ``(priority, insertion_order)`` — lower values sort first.
        """
        return (self.priority, self.insertion_order)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Wires all primitives into a single request handler.

    The orchestrator owns the request lifecycle:
    message -> context -> policy -> router -> runtime -> responder -> response

    All primitives are injected — none are created internally.

    Attributes:
        router: Intent classifier that selects a handler.
        runtime: Agent execution engine.
        responder: Output formatter for target channels.
        tools: Optional tool discovery and execution layer.
        memory: Optional tiered context storage.
        registry: Optional component catalog.
        policy: Optional policy enforcement engine.
    """

    def __init__(
        self,
        router: IntentRouter,
        runtime: AgentRuntime,
        responder: Responder,
        *,
        tools: ToolManager | None = None,
        memory: Memory | None = None,
        registry: Registry | None = None,
        policy: PolicyEngine | None = None,
        max_delegation_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    ) -> None:
        self._router = _wrap_router_if_registry(router, registry)
        self._runtime = runtime
        self._responder = responder
        self._tools = _wrap_tools_if_registry(tools, registry)
        self._memory = memory
        self._registry = registry
        self._policy = policy
        self._max_delegation_depth = max_delegation_depth
        self._middleware: dict[MiddlewareStage, list[_MiddlewareEntry]] = defaultdict(
            list
        )
        self._middleware_insertion_counter: int = 0
        self._error_handlers: list[MiddlewareErrorHandler] = []

    # -- Public API --------------------------------------------------------

    async def handle(
        self,
        message: str,
        *,
        ctx: ExecContext | None = None,
        channel: Channel | None = None,
    ) -> Response:
        """Process a message through the full pipeline.

        Steps:
            1. Create ``ExecContext`` if not provided.
            2. Policy: rate limit and budget check on the route action.
            3. Memory: recall relevant context for prompt enrichment.
            4. Middleware: ``BEFORE_ROUTE``.
            5. Router: classify intent, select handler.
            6. Middleware: ``BEFORE_INVOKE``.
            7. Policy: check invoke permission for the selected handler.
            8. Runtime: execute handler.
            9. Middleware: ``AFTER_INVOKE``.
            10. Memory: store the result.
            11. Responder: format output.
            12. Middleware: ``BEFORE_RESPOND``.

        Args:
            message: User message.
            ctx: Optional pre-built context. Created if ``None``.
            channel: Target channel. Defaults to ``API_CHANNEL``.

        Returns:
            Formatted ``Response``.

        Raises:
            PolicyDeniedError: If policy blocks the request.
        """
        ctx = self._create_or_validate_ctx(ctx)
        target_channel = channel or API_CHANNEL

        await self._check_policy(POLICY_ACTION_ROUTE, message, ctx)
        memory_ctx = await self._recall_memory(message, ctx)
        message = await self._run_middleware(MiddlewareStage.BEFORE_ROUTE, ctx, message)

        intent = await self._route(message, ctx)
        handler_name = self._pick_handler(intent)

        agent_input = self._build_agent_input(message, memory_ctx)
        agent_input = await self._run_middleware(
            MiddlewareStage.BEFORE_INVOKE, ctx, agent_input
        )

        await self._check_policy(POLICY_ACTION_INVOKE, handler_name, ctx)
        result = await self._invoke(handler_name, agent_input, ctx)
        result = await self._run_middleware(MiddlewareStage.AFTER_INVOKE, ctx, result)

        await self._store_memory(result, ctx)
        response = await self._format_response(result, target_channel, ctx)
        response = await self._run_middleware(
            MiddlewareStage.BEFORE_RESPOND, ctx, response
        )

        return response

    async def stream(
        self,
        message: str,
        *,
        ctx: ExecContext | None = None,
        channel: Channel | None = None,
    ) -> AsyncIterator[str]:
        """Process a message with streaming output.

        Same pipeline as ``handle()`` but attaches an ``InMemoryStreamSink``
        to the context and yields chunks as they arrive from the runtime.

        Args:
            message: User message.
            ctx: Optional pre-built context. Created if ``None``.
            channel: Target channel. Defaults to ``API_CHANNEL``.

        Yields:
            String chunks as they are produced by the runtime.

        Raises:
            PolicyDeniedError: If policy blocks the request.
        """
        sink = InMemoryStreamSink()
        ctx = self._create_or_validate_ctx(ctx)
        ctx.stream = sink

        task = asyncio.create_task(
            self.handle(message, ctx=ctx, channel=channel)
        )

        read_index = 0
        while not task.done():
            if read_index < len(sink.chunks):
                yield sink.chunks[read_index]
                read_index += 1
            else:
                await asyncio.sleep(STREAM_POLL_INTERVAL_SECONDS)

        # Drain any remaining chunks after the task completes
        while read_index < len(sink.chunks):
            yield sink.chunks[read_index]
            read_index += 1

        # Re-raise exceptions from the pipeline task
        task.result()

    async def delegate(
        self,
        handler_name: str,
        message: str,
        ctx: ExecContext,
    ) -> AgentResult:
        """Delegate execution to another handler with a child context.

        Creates a child ``ExecContext`` from *ctx*, checks agent permissions,
        enforces the delegation depth limit, invokes the handler through the
        runtime, and accumulates the child's token usage back to the parent.

        Args:
            handler_name: Name of the handler to delegate to.
            message: Message to pass as the delegated handler's input.
            ctx: Parent execution context.

        Returns:
            ``AgentResult`` from the delegated handler, or an error result
            if the depth limit is exceeded or permissions deny the delegation.
        """
        if not handler_name:
            return self._build_delegation_error("handler_name must not be empty")

        if not ctx.permissions.can_use_agent(handler_name):
            ctx.add_event(
                "delegation.denied",
                handler=handler_name,
                reason="permission_denied",
            )
            return self._build_delegation_error(
                f"permission denied: cannot delegate to '{handler_name}'"
            )

        child_ctx = ctx.child(handler_name)

        if child_ctx.depth > self._max_delegation_depth:
            ctx.add_event(
                "delegation.depth_exceeded",
                handler=handler_name,
                depth=str(child_ctx.depth),
                max_depth=str(self._max_delegation_depth),
            )
            return self._build_delegation_error(
                DELEGATION_DEPTH_EXCEEDED_TEMPLATE.format(
                    n=self._max_delegation_depth
                )
            )

        agent_input = AgentInput(message=message)
        result = await self._runtime.invoke(handler_name, agent_input, child_ctx)

        ctx.record_tokens(child_ctx.token_usage)
        return result

    def use(
        self,
        stage: MiddlewareStage,
        handler: MiddlewareHandler,
        *,
        priority: int = DEFAULT_MIDDLEWARE_PRIORITY,
    ) -> None:
        """Register middleware for a pipeline stage.

        Middleware runs in priority order (lower = earlier). Handlers with
        equal priority preserve registration order. Each handler receives the
        current context and payload. Returning a non-``None`` value replaces
        the payload for subsequent handlers and the next pipeline stage.

        Args:
            stage: Pipeline stage to hook into.
            handler: Async callable ``(ctx, payload) -> payload | None``.
            priority: Execution order (lower runs first). Defaults to 100.
        """
        entry = _MiddlewareEntry(handler, priority, self._middleware_insertion_counter)
        self._middleware_insertion_counter += 1
        self._middleware[stage].append(entry)
        self._middleware[stage].sort(key=_MiddlewareEntry.sort_key)

    def on_error(self, handler: MiddlewareErrorHandler) -> MiddlewareErrorHandler:
        """Register an error handler for middleware failures.

        When a middleware handler raises an exception, all registered error
        handlers are called before the pipeline continues to the next stage.

        Can be used as a decorator::

            @orchestrator.on_error
            async def handle_error(err, stage, ctx):
                logging.error("Middleware failed: %s", err)

        Args:
            handler: Async callable ``(error, stage, ctx) -> None``.

        Returns:
            The handler unchanged, for decorator use.
        """
        self._error_handlers.append(handler)
        return handler

    def before_route(
        self,
        handler: MiddlewareHandler | None = None,
        *,
        priority: int = DEFAULT_MIDDLEWARE_PRIORITY,
    ) -> MiddlewareHandler | Callable[[MiddlewareHandler], MiddlewareHandler]:
        """Decorator to register ``BEFORE_ROUTE`` middleware.

        Can be used with or without arguments::

            @orchestrator.before_route
            async def log_request(ctx, payload): ...

            @orchestrator.before_route(priority=10)
            async def early_check(ctx, payload): ...

        Args:
            handler: The middleware function (when used without parentheses).
            priority: Execution order (lower runs first). Defaults to 100.

        Returns:
            The handler (direct use) or a decorator (parameterised use).
        """
        return self._register_decorator(MiddlewareStage.BEFORE_ROUTE, handler, priority)

    def before_invoke(
        self,
        handler: MiddlewareHandler | None = None,
        *,
        priority: int = DEFAULT_MIDDLEWARE_PRIORITY,
    ) -> MiddlewareHandler | Callable[[MiddlewareHandler], MiddlewareHandler]:
        """Decorator to register ``BEFORE_INVOKE`` middleware.

        Can be used with or without arguments::

            @orchestrator.before_invoke
            async def check_permission(ctx, payload): ...

            @orchestrator.before_invoke(priority=50)
            async def check_early(ctx, payload): ...

        Args:
            handler: The middleware function (when used without parentheses).
            priority: Execution order (lower runs first). Defaults to 100.

        Returns:
            The handler (direct use) or a decorator (parameterised use).
        """
        return self._register_decorator(MiddlewareStage.BEFORE_INVOKE, handler, priority)

    def after_invoke(
        self,
        handler: MiddlewareHandler | None = None,
        *,
        priority: int = DEFAULT_MIDDLEWARE_PRIORITY,
    ) -> MiddlewareHandler | Callable[[MiddlewareHandler], MiddlewareHandler]:
        """Decorator to register ``AFTER_INVOKE`` middleware.

        Can be used with or without arguments::

            @orchestrator.after_invoke
            async def record_usage(ctx, payload): ...

            @orchestrator.after_invoke(priority=200)
            async def late_check(ctx, payload): ...

        Args:
            handler: The middleware function (when used without parentheses).
            priority: Execution order (lower runs first). Defaults to 100.

        Returns:
            The handler (direct use) or a decorator (parameterised use).
        """
        return self._register_decorator(MiddlewareStage.AFTER_INVOKE, handler, priority)

    def before_respond(
        self,
        handler: MiddlewareHandler | None = None,
        *,
        priority: int = DEFAULT_MIDDLEWARE_PRIORITY,
    ) -> MiddlewareHandler | Callable[[MiddlewareHandler], MiddlewareHandler]:
        """Decorator to register ``BEFORE_RESPOND`` middleware.

        Can be used with or without arguments::

            @orchestrator.before_respond
            async def format_output(ctx, payload): ...

            @orchestrator.before_respond(priority=10)
            async def early_format(ctx, payload): ...

        Args:
            handler: The middleware function (when used without parentheses).
            priority: Execution order (lower runs first). Defaults to 100.

        Returns:
            The handler (direct use) or a decorator (parameterised use).
        """
        return self._register_decorator(MiddlewareStage.BEFORE_RESPOND, handler, priority)

    def _register_decorator(
        self,
        stage: MiddlewareStage,
        handler: MiddlewareHandler | None,
        priority: int,
    ) -> MiddlewareHandler | Callable[[MiddlewareHandler], MiddlewareHandler]:
        """Shared logic for decorator-style middleware registration.

        Handles both bare decorator (``@orch.before_route``) and
        parameterised decorator (``@orch.before_route(priority=10)``).

        Args:
            stage: Pipeline stage to register at.
            handler: The function if used as a bare decorator, else ``None``.
            priority: Execution priority.

        Returns:
            The handler itself (bare) or a wrapper decorator (parameterised).
        """
        if handler is not None:
            self.use(stage, handler, priority=priority)
            return handler

        def decorator(fn: MiddlewareHandler) -> MiddlewareHandler:
            self.use(stage, fn, priority=priority)
            return fn

        return decorator

    # -- Private helpers ---------------------------------------------------

    def _build_delegation_error(self, error_message: str) -> AgentResult:
        """Build an ERROR ``AgentResult`` for delegation failures.

        Args:
            error_message: Human-readable description of what went wrong.

        Returns:
            ``AgentResult`` with ERROR status and the given error message.
        """
        return AgentResult(
            status=AgentStatus.ERROR,
            error=error_message,
        )

    def _create_or_validate_ctx(self, ctx: ExecContext | None) -> ExecContext:
        """Return the provided context or create a fresh one.

        Args:
            ctx: Caller-supplied context, or ``None``.

        Returns:
            A valid ``ExecContext`` ready for the pipeline.
        """
        if ctx is not None:
            return ctx
        return ExecContext.create()

    async def _check_policy(
        self, action_kind: str, target: str, ctx: ExecContext
    ) -> None:
        """Evaluate a policy action and raise on denial.

        No-ops when the policy engine is not configured.

        Args:
            action_kind: The type of action (e.g. ``"route"``, ``"invoke_agent"``).
            target: What the action targets (message text or handler name).
            ctx: Current execution context.

        Raises:
            PolicyDeniedError: If the policy engine denies the action.
        """
        if self._policy is None:
            return

        subject = ctx.user_id or "anonymous"
        action = PolicyAction(kind=action_kind, subject=subject, target=target)
        decision = await self._policy.evaluate(action, ctx)
        await self._policy.record(action, decision, ctx)

        if not decision.allowed:
            ctx.add_event("policy.denied", action_kind=action_kind, target=target)
            raise PolicyDeniedError(decision)

    async def _recall_memory(
        self, message: str, ctx: ExecContext
    ) -> list[dict[str, str]]:
        """Recall relevant conversation history from memory.

        Returns an empty list when memory is not configured.

        Args:
            message: User message to use as the recall query.
            ctx: Current execution context.

        Returns:
            Conversation history entries from memory.
        """
        if self._memory is None:
            return []

        memory_ctx = await self._memory.recall(message, ctx)
        return memory_ctx.conversation

    async def _route(self, message: str, ctx: ExecContext) -> IntentResult:
        """Classify the message intent and return routing result.

        Args:
            message: User message to classify.
            ctx: Current execution context.

        Returns:
            ``IntentResult`` with ranked handler candidates.
        """
        return await self._router.classify(message, ctx)

    def _pick_handler(self, intent: IntentResult) -> str:
        """Extract the best handler name from an intent result.

        Falls back to ``FALLBACK_HANDLER`` when the router returns
        no candidates.

        Args:
            intent: Routing result with ranked handler candidates.

        Returns:
            Handler name to invoke.
        """
        best = intent.best_handler
        if best is None:
            return FALLBACK_HANDLER
        return best.name

    def _build_agent_input(
        self, message: str, history: list[dict[str, str]]
    ) -> AgentInput:
        """Construct an ``AgentInput`` from the message and memory context.

        Args:
            message: User message.
            history: Conversation history from memory recall.

        Returns:
            Immutable ``AgentInput`` ready for the runtime.
        """
        return AgentInput(message=message, history=history)

    async def _invoke(
        self, handler: str, agent_input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Execute the selected handler and optionally record stats.

        Measures wall-clock duration and records success/failure in the
        registry when one is configured. Recording failures are logged
        but do not affect the result.

        Args:
            handler: Handler name to invoke.
            agent_input: Structured input for the handler.
            ctx: Current execution context.

        Returns:
            ``AgentResult`` from the runtime.
        """
        start = time.monotonic()
        result = await self._runtime.invoke(handler, agent_input, ctx)
        duration_ms = (time.monotonic() - start) * 1000
        await self._record_invocation(handler, result, duration_ms)
        return result

    async def _record_invocation(
        self, handler: str, result: AgentResult, duration_ms: float
    ) -> None:
        """Record invocation metrics in the registry.

        No-ops when the registry is not configured. Silently catches
        ``KeyError`` when the handler is not registered.

        Args:
            handler: Handler name that was invoked.
            result: The agent result to classify as success or failure.
            duration_ms: Wall-clock duration in milliseconds.
        """
        if self._registry is None:
            return

        try:
            entry = await self._registry.resolve(handler, ExecContext.create())
            if entry is None:
                return
            if result.status == AgentStatus.SUCCESS:
                entry.stats.record_success(duration_ms)
            else:
                entry.stats.record_failure(duration_ms)
        except (KeyError, Exception):
            # Recording should never break the request pipeline.
            pass

    async def _store_memory(self, result: AgentResult, ctx: ExecContext) -> None:
        """Persist a successful agent result to memory.

        Skips storage when memory is not configured or the result
        indicates a non-success status.

        Args:
            result: Agent result to potentially store.
            ctx: Current execution context.
        """
        if self._memory is None:
            return
        if result.status != AgentStatus.SUCCESS:
            return

        from nerva.memory import MemoryEvent, MemoryTier

        event = MemoryEvent(
            content=result.output,
            tier=MemoryTier.HOT,
            source=result.handler,
        )
        await self._memory.store(event, ctx)

    async def _format_response(
        self, result: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Format an agent result for the target channel.

        Args:
            result: Agent result to format.
            channel: Target delivery channel.
            ctx: Current execution context.

        Returns:
            Formatted ``Response``.
        """
        return await self._responder.format(result, channel, ctx)

    async def _run_middleware(
        self, stage: MiddlewareStage, ctx: ExecContext, payload: object
    ) -> object:
        """Run all middleware for a stage in priority order.

        Each handler may return a replacement payload or ``None`` to
        keep the current one. Handlers execute sequentially — the output
        of one feeds into the next.

        If a handler raises an exception, remaining handlers in this stage
        are skipped, error handlers are notified via ``_emit_middleware_error``,
        and the pipeline continues with the last good payload.

        Args:
            stage: Pipeline stage being executed.
            ctx: Current execution context.
            payload: Current payload entering this stage.

        Returns:
            The (possibly replaced) payload after all handlers have run.
        """
        for entry in self._middleware[stage]:
            try:
                result = await entry.handler(ctx, payload)
                if result is not None:
                    payload = result
            except Exception as exc:
                await self._emit_middleware_error(exc, stage, ctx)
                break
        return payload

    async def _emit_middleware_error(
        self, error: Exception, stage: MiddlewareStage, ctx: ExecContext
    ) -> None:
        """Notify error handlers and emit a trace event for a middleware failure.

        Args:
            error: The exception raised by the middleware handler.
            stage: The pipeline stage where the error occurred.
            ctx: Current execution context.
        """
        ctx.add_event(
            "middleware.error",
            stage=stage.value,
            error=str(error),
            error_type=type(error).__name__,
        )
        for handler in self._error_handlers:
            try:
                await handler(error, stage, ctx)
            except Exception:
                logger.warning(
                    "Error handler failed while handling middleware error in %s",
                    stage.value,
                    exc_info=True,
                )


def _wrap_router_if_registry(
    router: IntentRouter, registry: Registry | None
) -> IntentRouter:
    """Wrap the router with registry-aware filtering when a registry is provided.

    Args:
        router: The original intent router.
        registry: Optional component registry.

    Returns:
        A ``RegistryAwareRouter`` if registry is provided, otherwise the
        original router unchanged.
    """
    if registry is None:
        return router
    from nerva.router.registry_aware import RegistryAwareRouter

    return RegistryAwareRouter(router, registry)  # type: ignore[return-value]


def _wrap_tools_if_registry(
    tools: ToolManager | None, registry: Registry | None
) -> ToolManager | None:
    """Wrap the tool manager with registry-aware filtering when both are provided.

    Args:
        tools: The original tool manager, or ``None``.
        registry: Optional component registry.

    Returns:
        A ``RegistryAwareToolManager`` if both are provided, otherwise
        the original tools unchanged.
    """
    if tools is None or registry is None:
        return tools
    from nerva.tools.registry_aware import RegistryAwareToolManager

    return RegistryAwareToolManager(tools, registry)  # type: ignore[return-value]


__all__ = [
    "Orchestrator",
    "MiddlewareStage",
    "MiddlewareHandler",
    "MiddlewareErrorHandler",
    "PolicyDeniedError",
    "POLICY_ACTION_ROUTE",
    "POLICY_ACTION_INVOKE",
    "FALLBACK_HANDLER",
    "STREAM_POLL_INTERVAL_SECONDS",
    "DEFAULT_MIDDLEWARE_PRIORITY",
    "DEFAULT_MAX_DELEGATION_DEPTH",
    "DELEGATION_DEPTH_EXCEEDED_TEMPLATE",
]
