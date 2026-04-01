"""Spy wrappers — record calls and support expectations over real implementations.

Each spy wraps a real Nerva primitive, delegating all method calls to the inner
implementation while recording every invocation. When expectations are set via
``expect_*()`` methods, the spy returns the configured value instead of calling
the real implementation (FIFO queue, falls back to passthrough when exhausted).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nerva.context import ExecContext
    from nerva.memory import Memory, MemoryContext, MemoryEvent
    from nerva.policy import PolicyAction, PolicyDecision, PolicyEngine
    from nerva.responder import AgentResult, Channel, Responder, Response
    from nerva.router import IntentResult, IntentRouter
    from nerva.runtime import AgentInput, AgentRuntime
    from nerva.tools import ToolManager, ToolResult, ToolSpec


# ---------------------------------------------------------------------------
# Call records — typed dataclasses for each recorded invocation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifyCall:
    """Record of a single ``IntentRouter.classify()`` invocation.

    Attributes:
        message: The user message that was classified.
        ctx: Execution context at the time of the call.
        result: The IntentResult returned (from expectation or real impl).
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    message: str
    ctx: ExecContext
    result: IntentResult
    timestamp: float
    was_expected: bool


@dataclass(frozen=True)
class InvokeCall:
    """Record of a single ``AgentRuntime.invoke()`` invocation.

    Attributes:
        handler: Handler name that was invoked.
        input: The AgentInput passed to the handler.
        ctx: Execution context at the time of the call.
        result: The AgentResult returned.
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    handler: str
    input: AgentInput
    ctx: ExecContext
    result: AgentResult
    timestamp: float
    was_expected: bool


@dataclass(frozen=True)
class DelegateCall:
    """Record of a single ``AgentRuntime.delegate()`` invocation.

    Attributes:
        handler: Handler name that was delegated to.
        input: The AgentInput passed to the handler.
        parent_ctx: Parent execution context.
        result: The AgentResult returned.
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    handler: str
    input: AgentInput
    parent_ctx: ExecContext
    result: AgentResult
    timestamp: float
    was_expected: bool


@dataclass(frozen=True)
class FormatCall:
    """Record of a single ``Responder.format()`` invocation.

    Attributes:
        output: The AgentResult that was formatted.
        channel: Target delivery channel.
        ctx: Execution context at the time of the call.
        result: The Response returned.
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    output: AgentResult
    channel: Channel
    ctx: ExecContext
    result: Response
    timestamp: float
    was_expected: bool


@dataclass(frozen=True)
class RecallCall:
    """Record of a single ``Memory.recall()`` invocation.

    Attributes:
        query: The search query.
        ctx: Execution context at the time of the call.
        result: The MemoryContext returned.
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    query: str
    ctx: ExecContext
    result: MemoryContext
    timestamp: float
    was_expected: bool


@dataclass(frozen=True)
class StoreCall:
    """Record of a single ``Memory.store()`` invocation.

    Attributes:
        event: The MemoryEvent that was stored.
        ctx: Execution context at the time of the call.
        timestamp: Unix timestamp when the call was made.
    """

    event: MemoryEvent
    ctx: ExecContext
    timestamp: float


@dataclass(frozen=True)
class EvaluateCall:
    """Record of a single ``PolicyEngine.evaluate()`` invocation.

    Attributes:
        action: The PolicyAction that was evaluated.
        ctx: Execution context at the time of the call.
        result: The PolicyDecision returned.
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    action: PolicyAction
    ctx: ExecContext
    result: PolicyDecision
    timestamp: float
    was_expected: bool


@dataclass(frozen=True)
class PolicyRecordCall:
    """Record of a single ``PolicyEngine.record()`` invocation.

    Attributes:
        action: The PolicyAction that was recorded.
        decision: The PolicyDecision that was recorded.
        ctx: Execution context at the time of the call.
        timestamp: Unix timestamp when the call was made.
    """

    action: PolicyAction
    decision: PolicyDecision
    ctx: ExecContext
    timestamp: float


@dataclass(frozen=True)
class DiscoverToolsCall:
    """Record of a single ``ToolManager.discover()`` invocation.

    Attributes:
        ctx: Execution context at the time of the call.
        result: The list of ToolSpecs returned.
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    ctx: ExecContext
    result: list[ToolSpec]
    timestamp: float
    was_expected: bool


@dataclass(frozen=True)
class ToolCallRecord:
    """Record of a single ``ToolManager.call()`` invocation.

    Attributes:
        tool_name: Name of the tool that was called.
        args: Arguments passed to the tool.
        ctx: Execution context at the time of the call.
        result: The ToolResult returned.
        timestamp: Unix timestamp when the call was made.
        was_expected: True if the result came from an expectation queue.
    """

    tool_name: str
    args: dict[str, object]
    ctx: ExecContext
    result: ToolResult
    timestamp: float
    was_expected: bool


# ---------------------------------------------------------------------------
# SpyRouter
# ---------------------------------------------------------------------------


class SpyRouter:
    """Spy wrapper around an ``IntentRouter`` implementation.

    Records every ``classify()`` call. Supports expectation-setting via
    ``expect_handler()`` and ``expect_intent()`` — when expectations are
    queued, they are consumed FIFO before falling back to the real implementation.

    Attributes:
        inner: The wrapped IntentRouter implementation.
        classify_calls: Ordered list of recorded classify invocations.
    """

    def __init__(self, inner: IntentRouter) -> None:
        self._inner = inner
        self.classify_calls: list[ClassifyCall] = []
        self._expectations: deque[IntentResult] = deque()

    @property
    def inner(self) -> IntentRouter:
        """The wrapped IntentRouter implementation."""
        return self._inner

    @property
    def pending_expectations(self) -> int:
        """Number of unconsumed expectations remaining."""
        return len(self._expectations)

    def expect_handler(self, handler_name: str, *, confidence: float = 0.95) -> None:
        """Queue an expectation that classify() will return the given handler.

        Args:
            handler_name: Handler name to return.
            confidence: Confidence score for the result.
        """
        from nerva.router import HandlerCandidate, IntentResult

        candidate = HandlerCandidate(name=handler_name, score=confidence, reason="expected")
        result = IntentResult(
            intent=handler_name,
            confidence=confidence,
            handlers=[candidate],
        )
        self._expectations.append(result)

    def expect_intent(self, result: IntentResult) -> None:
        """Queue a full IntentResult expectation.

        Args:
            result: The IntentResult to return on the next classify() call.
        """
        self._expectations.append(result)

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Classify a message, returning an expectation or delegating to the real router.

        Args:
            message: Raw user message text.
            ctx: Execution context.

        Returns:
            IntentResult from expectation queue or real implementation.
        """
        was_expected = len(self._expectations) > 0
        if was_expected:
            result = self._expectations.popleft()
        else:
            result = await self._inner.classify(message, ctx)

        self.classify_calls.append(
            ClassifyCall(
                message=message,
                ctx=ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    def reset(self) -> None:
        """Clear all recorded calls and pending expectations."""
        self.classify_calls.clear()
        self._expectations.clear()

    def verify_expectations_consumed(self) -> None:
        """Assert that all expectations have been consumed.

        Raises:
            AssertionError: If there are unconsumed expectations.
        """
        assert self.pending_expectations == 0, (
            f"SpyRouter has {self.pending_expectations} unconsumed expectation(s)"
        )


# ---------------------------------------------------------------------------
# SpyRuntime
# ---------------------------------------------------------------------------


class SpyRuntime:
    """Spy wrapper around an ``AgentRuntime`` implementation.

    Records ``invoke()``, ``invoke_chain()``, and ``delegate()`` calls.
    Supports expectation-setting via ``expect_result()`` for invoke calls.

    Attributes:
        inner: The wrapped AgentRuntime implementation.
        invoke_calls: Ordered list of recorded invoke invocations.
        delegate_calls: Ordered list of recorded delegate invocations.
    """

    def __init__(self, inner: AgentRuntime) -> None:
        self._inner = inner
        self.invoke_calls: list[InvokeCall] = []
        self.delegate_calls: list[DelegateCall] = []
        self._invoke_expectations: deque[AgentResult] = deque()
        self._delegate_expectations: deque[AgentResult] = deque()

    @property
    def inner(self) -> AgentRuntime:
        """The wrapped AgentRuntime implementation."""
        return self._inner

    @property
    def pending_expectations(self) -> int:
        """Number of unconsumed expectations remaining."""
        return len(self._invoke_expectations) + len(self._delegate_expectations)

    def expect_result(self, result: AgentResult) -> None:
        """Queue an expectation for the next ``invoke()`` call.

        Args:
            result: The AgentResult to return.
        """
        self._invoke_expectations.append(result)

    def expect_llm_response(self, output: str) -> None:
        """Queue an expectation for a successful invoke() with the given output.

        Args:
            output: The output text to return.
        """
        from nerva.runtime import AgentResult, AgentStatus

        self._invoke_expectations.append(
            AgentResult(status=AgentStatus.SUCCESS, output=output)
        )

    def expect_delegate_result(self, result: AgentResult) -> None:
        """Queue an expectation for the next ``delegate()`` call.

        Args:
            result: The AgentResult to return.
        """
        self._delegate_expectations.append(result)

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Invoke a handler, returning an expectation or delegating to the real runtime.

        Args:
            handler: Handler name.
            input: Agent input.
            ctx: Execution context.

        Returns:
            AgentResult from expectation queue or real implementation.
        """
        was_expected = len(self._invoke_expectations) > 0
        if was_expected:
            result = self._invoke_expectations.popleft()
        else:
            result = await self._inner.invoke(handler, input, ctx)

        self.invoke_calls.append(
            InvokeCall(
                handler=handler,
                input=input,
                ctx=ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    async def invoke_chain(
        self, handlers: list[str], input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Chain invocation — delegates to inner implementation.

        Args:
            handlers: Ordered list of handler names.
            input: Initial agent input.
            ctx: Execution context.

        Returns:
            AgentResult from the chain.
        """
        return await self._inner.invoke_chain(handlers, input, ctx)

    async def delegate(
        self, handler: str, input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Delegate to a handler, returning an expectation or delegating to real runtime.

        Args:
            handler: Handler name to delegate to.
            input: Agent input.
            parent_ctx: Parent execution context.

        Returns:
            AgentResult from expectation queue or real implementation.
        """
        was_expected = len(self._delegate_expectations) > 0
        if was_expected:
            result = self._delegate_expectations.popleft()
        else:
            result = await self._inner.delegate(handler, input, parent_ctx)

        self.delegate_calls.append(
            DelegateCall(
                handler=handler,
                input=input,
                parent_ctx=parent_ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    def reset(self) -> None:
        """Clear all recorded calls and pending expectations."""
        self.invoke_calls.clear()
        self.delegate_calls.clear()
        self._invoke_expectations.clear()
        self._delegate_expectations.clear()

    def verify_expectations_consumed(self) -> None:
        """Assert that all expectations have been consumed.

        Raises:
            AssertionError: If there are unconsumed expectations.
        """
        assert self.pending_expectations == 0, (
            f"SpyRuntime has {self.pending_expectations} unconsumed expectation(s)"
        )


# ---------------------------------------------------------------------------
# SpyResponder
# ---------------------------------------------------------------------------


class SpyResponder:
    """Spy wrapper around a ``Responder`` implementation.

    Records every ``format()`` call. Supports expectation-setting via
    ``expect_response()``.

    Attributes:
        inner: The wrapped Responder implementation.
        format_calls: Ordered list of recorded format invocations.
    """

    def __init__(self, inner: Responder) -> None:
        self._inner = inner
        self.format_calls: list[FormatCall] = []
        self._expectations: deque[Response] = deque()

    @property
    def inner(self) -> Responder:
        """The wrapped Responder implementation."""
        return self._inner

    @property
    def pending_expectations(self) -> int:
        """Number of unconsumed expectations remaining."""
        return len(self._expectations)

    def expect_response(self, response: Response) -> None:
        """Queue an expectation for the next ``format()`` call.

        Args:
            response: The Response to return.
        """
        self._expectations.append(response)

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Format agent output, returning an expectation or delegating to real responder.

        Args:
            output: Raw agent result.
            channel: Target delivery channel.
            ctx: Execution context.

        Returns:
            Response from expectation queue or real implementation.
        """
        was_expected = len(self._expectations) > 0
        if was_expected:
            result = self._expectations.popleft()
        else:
            result = await self._inner.format(output, channel, ctx)

        self.format_calls.append(
            FormatCall(
                output=output,
                channel=channel,
                ctx=ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    def reset(self) -> None:
        """Clear all recorded calls and pending expectations."""
        self.format_calls.clear()
        self._expectations.clear()

    def verify_expectations_consumed(self) -> None:
        """Assert that all expectations have been consumed.

        Raises:
            AssertionError: If there are unconsumed expectations.
        """
        assert self.pending_expectations == 0, (
            f"SpyResponder has {self.pending_expectations} unconsumed expectation(s)"
        )


# ---------------------------------------------------------------------------
# SpyMemory
# ---------------------------------------------------------------------------


class SpyMemory:
    """Spy wrapper around a ``Memory`` implementation.

    Records ``recall()``, ``store()``, and ``consolidate()`` calls.
    Supports expectation-setting via ``expect_recall()``.

    Attributes:
        inner: The wrapped Memory implementation.
        recall_calls: Ordered list of recorded recall invocations.
        store_calls: Ordered list of recorded store invocations.
    """

    def __init__(self, inner: Memory) -> None:
        self._inner = inner
        self.recall_calls: list[RecallCall] = []
        self.store_calls: list[StoreCall] = []
        self._recall_expectations: deque[MemoryContext] = deque()

    @property
    def inner(self) -> Memory:
        """The wrapped Memory implementation."""
        return self._inner

    @property
    def pending_expectations(self) -> int:
        """Number of unconsumed expectations remaining."""
        return len(self._recall_expectations)

    def expect_recall(self, memory_ctx: MemoryContext) -> None:
        """Queue an expectation for the next ``recall()`` call.

        Args:
            memory_ctx: The MemoryContext to return.
        """
        self._recall_expectations.append(memory_ctx)

    async def recall(self, query: str, ctx: ExecContext) -> MemoryContext:
        """Recall context, returning an expectation or delegating to real memory.

        Args:
            query: Search query for relevant memories.
            ctx: Execution context.

        Returns:
            MemoryContext from expectation queue or real implementation.
        """
        was_expected = len(self._recall_expectations) > 0
        if was_expected:
            result = self._recall_expectations.popleft()
        else:
            result = await self._inner.recall(query, ctx)

        self.recall_calls.append(
            RecallCall(
                query=query,
                ctx=ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    async def store(self, event: MemoryEvent, ctx: ExecContext) -> None:
        """Store an event, recording the call and delegating to real memory.

        Args:
            event: Memory event to store.
            ctx: Execution context.
        """
        self.store_calls.append(
            StoreCall(event=event, ctx=ctx, timestamp=time.time())
        )
        await self._inner.store(event, ctx)

    async def consolidate(self, ctx: ExecContext) -> None:
        """Consolidate memories, delegating to real implementation.

        Args:
            ctx: Execution context.
        """
        await self._inner.consolidate(ctx)

    def reset(self) -> None:
        """Clear all recorded calls and pending expectations."""
        self.recall_calls.clear()
        self.store_calls.clear()
        self._recall_expectations.clear()

    def verify_expectations_consumed(self) -> None:
        """Assert that all expectations have been consumed.

        Raises:
            AssertionError: If there are unconsumed expectations.
        """
        assert self.pending_expectations == 0, (
            f"SpyMemory has {self.pending_expectations} unconsumed expectation(s)"
        )


# ---------------------------------------------------------------------------
# SpyPolicy
# ---------------------------------------------------------------------------


class SpyPolicy:
    """Spy wrapper around a ``PolicyEngine`` implementation.

    Records ``evaluate()`` and ``record()`` calls. Supports expectation-setting
    via ``expect_allow()`` and ``expect_deny()``.

    Attributes:
        inner: The wrapped PolicyEngine implementation.
        evaluate_calls: Ordered list of recorded evaluate invocations.
        record_calls: Ordered list of recorded record invocations.
    """

    def __init__(self, inner: PolicyEngine) -> None:
        self._inner = inner
        self.evaluate_calls: list[EvaluateCall] = []
        self.record_calls: list[PolicyRecordCall] = []
        self._expectations: deque[PolicyDecision] = deque()

    @property
    def inner(self) -> PolicyEngine:
        """The wrapped PolicyEngine implementation."""
        return self._inner

    @property
    def pending_expectations(self) -> int:
        """Number of unconsumed expectations remaining."""
        return len(self._expectations)

    def expect_allow(self) -> None:
        """Queue an expectation that the next ``evaluate()`` will allow."""
        from nerva.policy import ALLOW

        self._expectations.append(ALLOW)

    def expect_deny(self, reason: str = "denied by test") -> None:
        """Queue an expectation that the next ``evaluate()`` will deny.

        Args:
            reason: Reason string for the denial.
        """
        from nerva.policy import PolicyDecision

        self._expectations.append(PolicyDecision(allowed=False, reason=reason))

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Evaluate a policy action, returning an expectation or delegating to real engine.

        Args:
            action: The action to evaluate.
            ctx: Execution context.

        Returns:
            PolicyDecision from expectation queue or real implementation.
        """
        was_expected = len(self._expectations) > 0
        if was_expected:
            result = self._expectations.popleft()
        else:
            result = await self._inner.evaluate(action, ctx)

        self.evaluate_calls.append(
            EvaluateCall(
                action=action,
                ctx=ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        """Record a policy decision, delegating to real engine.

        Args:
            action: The evaluated action.
            decision: The decision that was made.
            ctx: Execution context.
        """
        self.record_calls.append(
            PolicyRecordCall(
                action=action,
                decision=decision,
                ctx=ctx,
                timestamp=time.time(),
            )
        )
        await self._inner.record(action, decision, ctx)

    def reset(self) -> None:
        """Clear all recorded calls and pending expectations."""
        self.evaluate_calls.clear()
        self.record_calls.clear()
        self._expectations.clear()

    def verify_expectations_consumed(self) -> None:
        """Assert that all expectations have been consumed.

        Raises:
            AssertionError: If there are unconsumed expectations.
        """
        assert self.pending_expectations == 0, (
            f"SpyPolicy has {self.pending_expectations} unconsumed expectation(s)"
        )


# ---------------------------------------------------------------------------
# SpyToolManager
# ---------------------------------------------------------------------------


class SpyToolManager:
    """Spy wrapper around a ``ToolManager`` implementation.

    Records ``discover()`` and ``call()`` invocations. Supports
    expectation-setting via ``expect_tool_result()`` and ``expect_tools()``.

    Attributes:
        inner: The wrapped ToolManager implementation.
        discover_calls: Ordered list of recorded discover invocations.
        call_calls: Ordered list of recorded call invocations.
    """

    def __init__(self, inner: ToolManager) -> None:
        self._inner = inner
        self.discover_calls: list[DiscoverToolsCall] = []
        self.call_calls: list[ToolCallRecord] = []
        self._discover_expectations: deque[list[ToolSpec]] = deque()
        self._call_expectations: dict[str, deque[ToolResult]] = {}

    @property
    def inner(self) -> ToolManager:
        """The wrapped ToolManager implementation."""
        return self._inner

    @property
    def pending_expectations(self) -> int:
        """Number of unconsumed expectations remaining."""
        tool_count = sum(len(q) for q in self._call_expectations.values())
        return len(self._discover_expectations) + tool_count

    def expect_tools(self, tools: list[ToolSpec]) -> None:
        """Queue an expectation for the next ``discover()`` call.

        Args:
            tools: The list of ToolSpecs to return.
        """
        self._discover_expectations.append(tools)

    def expect_tool_result(self, tool_name: str, result: ToolResult) -> None:
        """Queue an expectation for the next ``call()`` to a specific tool.

        Args:
            tool_name: Name of the tool this expectation applies to.
            result: The ToolResult to return.
        """
        if tool_name not in self._call_expectations:
            self._call_expectations[tool_name] = deque()
        self._call_expectations[tool_name].append(result)

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Discover tools, returning an expectation or delegating to real manager.

        Args:
            ctx: Execution context.

        Returns:
            List of ToolSpecs from expectation queue or real implementation.
        """
        was_expected = len(self._discover_expectations) > 0
        if was_expected:
            result = self._discover_expectations.popleft()
        else:
            result = await self._inner.discover(ctx)

        self.discover_calls.append(
            DiscoverToolsCall(
                ctx=ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Call a tool, returning an expectation or delegating to real manager.

        Args:
            tool: Tool name to invoke.
            args: Arguments for the tool.
            ctx: Execution context.

        Returns:
            ToolResult from expectation queue or real implementation.
        """
        tool_queue = self._call_expectations.get(tool)
        was_expected = tool_queue is not None and len(tool_queue) > 0
        if was_expected:
            result = tool_queue.popleft()
            if len(tool_queue) == 0:
                del self._call_expectations[tool]
        else:
            result = await self._inner.call(tool, args, ctx)

        self.call_calls.append(
            ToolCallRecord(
                tool_name=tool,
                args=args,
                ctx=ctx,
                result=result,
                timestamp=time.time(),
                was_expected=was_expected,
            )
        )
        return result

    def reset(self) -> None:
        """Clear all recorded calls and pending expectations."""
        self.discover_calls.clear()
        self.call_calls.clear()
        self._discover_expectations.clear()
        self._call_expectations.clear()

    def verify_expectations_consumed(self) -> None:
        """Assert that all expectations have been consumed.

        Raises:
            AssertionError: If there are unconsumed expectations.
        """
        assert self.pending_expectations == 0, (
            f"SpyToolManager has {self.pending_expectations} unconsumed expectation(s)"
        )
