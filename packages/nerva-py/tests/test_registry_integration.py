"""Tests for N-158 (registry integration) and N-159 (invocation recording)."""

from __future__ import annotations

import pytest

from nerva.context import ExecContext
from nerva.orchestrator import FALLBACK_HANDLER, Orchestrator
from nerva.registry import (
    ComponentKind,
    HealthStatus,
    RegistryEntry,
    RegistryPatch,
)
from nerva.registry.inmemory import InMemoryRegistry
from nerva.responder import API_CHANNEL, Channel, Response
from nerva.router import HandlerCandidate, IntentResult
from nerva.router.registry_aware import RegistryAwareRouter
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.tools import ToolResult, ToolSpec, ToolStatus
from nerva.tools.registry_aware import RegistryAwareToolManager

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Mock primitives
# ---------------------------------------------------------------------------


class StubRouter:
    """Router returning configurable handler candidates."""

    _DEFAULT_CANDIDATES = [
        HandlerCandidate(name="agent_a", score=0.9, reason="best match"),
        HandlerCandidate(name="agent_b", score=0.7, reason="second"),
    ]

    def __init__(self, candidates: list[HandlerCandidate] | None = None) -> None:
        self._candidates = (
            candidates if candidates is not None else self._DEFAULT_CANDIDATES
        )

    async def classify(self, message: str, ctx: ExecContext) -> IntentResult:
        """Return fixed candidates."""
        return IntentResult(
            intent="test", confidence=0.9, handlers=list(self._candidates)
        )


class StubToolManager:
    """Tool manager returning configurable tool specs."""

    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools = tools or [
            ToolSpec(name="tool_x", description="Tool X"),
            ToolSpec(name="tool_y", description="Tool Y"),
        ]
        self.call_log: list[tuple[str, dict[str, object]]] = []

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Return configured tools."""
        return list(self._tools)

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Record the call and return success."""
        self.call_log.append((tool, args))
        return ToolResult(status=ToolStatus.SUCCESS, output="ok", duration_ms=1.0)


class StubRuntime:
    """Runtime returning a configurable result."""

    def __init__(
        self,
        output: str = "result",
        status: AgentStatus = AgentStatus.SUCCESS,
    ) -> None:
        self._output = output
        self._status = status
        self.invoke_calls: list[tuple[str, AgentInput]] = []

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Record the call and return a fixed result."""
        self.invoke_calls.append((handler, input))
        return AgentResult(status=self._status, output=self._output, handler=handler)

    async def invoke_chain(
        self, handlers: list[str], input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Chain — returns last."""
        result = AgentResult(status=self._status, output=self._output)
        for h in handlers:
            result = await self.invoke(h, input, ctx)
        return result

    async def delegate(
        self, handler: str, input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Delegate — same as invoke."""
        return await self.invoke(handler, input, parent_ctx)


class StubResponder:
    """Responder wrapping agent output in a Response."""

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Return a simple Response."""
        return Response(text=output.output, channel=channel)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register_agent(
    registry: InMemoryRegistry,
    name: str,
    health: HealthStatus = HealthStatus.HEALTHY,
    enabled: bool = True,
) -> RegistryEntry:
    """Register an agent entry in the registry."""
    entry = RegistryEntry(
        name=name,
        kind=ComponentKind.AGENT,
        description=f"Agent {name}",
        health=health,
        enabled=enabled,
    )
    await registry.register(entry, make_ctx())
    return entry


async def _register_tool(
    registry: InMemoryRegistry,
    name: str,
    health: HealthStatus = HealthStatus.HEALTHY,
    enabled: bool = True,
) -> RegistryEntry:
    """Register a tool entry in the registry."""
    entry = RegistryEntry(
        name=name,
        kind=ComponentKind.TOOL,
        description=f"Tool {name}",
        health=health,
        enabled=enabled,
    )
    await registry.register(entry, make_ctx())
    return entry


def _build_orchestrator(
    *,
    registry: InMemoryRegistry | None = None,
    runtime: StubRuntime | None = None,
    router: StubRouter | None = None,
    tools: StubToolManager | None = None,
) -> Orchestrator:
    """Build an Orchestrator with test defaults."""
    return Orchestrator(
        router=router or StubRouter(),
        runtime=runtime or StubRuntime(),
        responder=StubResponder(),
        registry=registry,
        tools=tools,
    )


# ===========================================================================
# N-158: RegistryAwareRouter
# ===========================================================================


class TestRegistryAwareRouter:
    """Verify RegistryAwareRouter filters candidates by health status."""

    @pytest.mark.asyncio
    async def test_healthy_candidates_pass_through(self) -> None:
        """All healthy candidates are retained."""
        registry = InMemoryRegistry()
        await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)
        await _register_agent(registry, "agent_b", HealthStatus.HEALTHY)

        wrapped = RegistryAwareRouter(StubRouter(), registry)
        result = await wrapped.classify("hello", make_ctx())

        names = [h.name for h in result.handlers]
        assert "agent_a" in names
        assert "agent_b" in names

    @pytest.mark.asyncio
    async def test_unavailable_candidates_are_filtered(self) -> None:
        """Unavailable candidates are removed."""
        registry = InMemoryRegistry()
        await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)
        await _register_agent(registry, "agent_b", HealthStatus.UNAVAILABLE)

        wrapped = RegistryAwareRouter(StubRouter(), registry)
        result = await wrapped.classify("hello", make_ctx())

        names = [h.name for h in result.handlers]
        assert "agent_a" in names
        assert "agent_b" not in names

    @pytest.mark.asyncio
    async def test_degraded_candidates_pass_through(self) -> None:
        """Degraded handlers are still available for routing."""
        registry = InMemoryRegistry()
        await _register_agent(registry, "agent_a", HealthStatus.DEGRADED)

        wrapped = RegistryAwareRouter(StubRouter(), registry)
        result = await wrapped.classify("hello", make_ctx())

        names = [h.name for h in result.handlers]
        assert "agent_a" in names

    @pytest.mark.asyncio
    async def test_unregistered_candidates_pass_through(self) -> None:
        """Candidates not in the registry are not blocked."""
        registry = InMemoryRegistry()
        # Only register agent_a; agent_b is unknown to registry
        await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        wrapped = RegistryAwareRouter(StubRouter(), registry)
        result = await wrapped.classify("hello", make_ctx())

        names = [h.name for h in result.handlers]
        assert "agent_a" in names
        assert "agent_b" in names

    @pytest.mark.asyncio
    async def test_all_unavailable_returns_empty_handlers(self) -> None:
        """When all candidates are unavailable, handlers list is empty."""
        registry = InMemoryRegistry()
        await _register_agent(registry, "agent_a", HealthStatus.UNAVAILABLE)
        await _register_agent(registry, "agent_b", HealthStatus.UNAVAILABLE)

        wrapped = RegistryAwareRouter(StubRouter(), registry)
        result = await wrapped.classify("hello", make_ctx())

        assert result.handlers == []

    @pytest.mark.asyncio
    async def test_intent_and_confidence_preserved(self) -> None:
        """The wrapper preserves intent and confidence from the inner router."""
        registry = InMemoryRegistry()
        wrapped = RegistryAwareRouter(StubRouter(), registry)
        result = await wrapped.classify("hello", make_ctx())

        assert result.intent == "test"
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_empty_candidates_list(self) -> None:
        """Router with no candidates still works."""
        registry = InMemoryRegistry()
        inner = StubRouter(candidates=[])
        wrapped = RegistryAwareRouter(inner, registry)
        result = await wrapped.classify("hello", make_ctx())

        assert result.handlers == []


# ===========================================================================
# N-158: RegistryAwareToolManager
# ===========================================================================


class TestRegistryAwareToolManager:
    """Verify RegistryAwareToolManager filters tools by registry status."""

    @pytest.mark.asyncio
    async def test_healthy_tools_pass_through(self) -> None:
        """All healthy tools are returned by discover."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.HEALTHY)
        await _register_tool(registry, "tool_y", HealthStatus.HEALTHY)

        wrapped = RegistryAwareToolManager(StubToolManager(), registry)
        tools = await wrapped.discover(make_ctx())

        names = [t.name for t in tools]
        assert "tool_x" in names
        assert "tool_y" in names

    @pytest.mark.asyncio
    async def test_unavailable_tools_filtered_from_discovery(self) -> None:
        """Unavailable tools are excluded from discover."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.HEALTHY)
        await _register_tool(registry, "tool_y", HealthStatus.UNAVAILABLE)

        wrapped = RegistryAwareToolManager(StubToolManager(), registry)
        tools = await wrapped.discover(make_ctx())

        names = [t.name for t in tools]
        assert "tool_x" in names
        assert "tool_y" not in names

    @pytest.mark.asyncio
    async def test_unregistered_tools_pass_through(self) -> None:
        """Tools not in the registry are not blocked."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.HEALTHY)

        wrapped = RegistryAwareToolManager(StubToolManager(), registry)
        tools = await wrapped.discover(make_ctx())

        names = [t.name for t in tools]
        assert "tool_x" in names
        assert "tool_y" in names  # not registered, passes through

    @pytest.mark.asyncio
    async def test_call_delegates_to_inner_for_healthy_tool(self) -> None:
        """Calling a healthy tool delegates to the inner manager."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.HEALTHY)

        inner = StubToolManager()
        wrapped = RegistryAwareToolManager(inner, registry)
        result = await wrapped.call("tool_x", {"key": "val"}, make_ctx())

        assert result.status == ToolStatus.SUCCESS
        assert len(inner.call_log) == 1

    @pytest.mark.asyncio
    async def test_call_rejects_unavailable_tool(self) -> None:
        """Calling an unavailable tool returns NOT_FOUND without invoking inner."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.UNAVAILABLE)

        inner = StubToolManager()
        wrapped = RegistryAwareToolManager(inner, registry)
        result = await wrapped.call("tool_x", {}, make_ctx())

        assert result.status == ToolStatus.NOT_FOUND
        assert "unavailable" in (result.error or "").lower()
        assert len(inner.call_log) == 0

    @pytest.mark.asyncio
    async def test_call_allows_unregistered_tool(self) -> None:
        """Calling a tool not in the registry delegates normally."""
        registry = InMemoryRegistry()
        inner = StubToolManager()
        wrapped = RegistryAwareToolManager(inner, registry)
        result = await wrapped.call("unknown_tool", {}, make_ctx())

        assert result.status == ToolStatus.SUCCESS
        assert len(inner.call_log) == 1

    @pytest.mark.asyncio
    async def test_degraded_tool_is_callable(self) -> None:
        """Degraded tools are still callable."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.DEGRADED)

        inner = StubToolManager()
        wrapped = RegistryAwareToolManager(inner, registry)
        result = await wrapped.call("tool_x", {}, make_ctx())

        assert result.status == ToolStatus.SUCCESS


# ===========================================================================
# N-158: Orchestrator wiring
# ===========================================================================


class TestOrchestratorRegistryWiring:
    """Verify the orchestrator wires registry-aware wrappers automatically."""

    @pytest.mark.asyncio
    async def test_no_registry_uses_original_router(self) -> None:
        """Without a registry, the original router is used."""
        runtime = StubRuntime()
        orch = _build_orchestrator(runtime=runtime)

        resp = await orch.handle("hello")
        assert resp.text == "result"
        handler_name, _ = runtime.invoke_calls[0]
        assert handler_name == "agent_a"

    @pytest.mark.asyncio
    async def test_registry_filters_unavailable_handler(self) -> None:
        """With registry, unavailable handler is excluded from routing."""
        registry = InMemoryRegistry()
        await _register_agent(registry, "agent_a", HealthStatus.UNAVAILABLE)
        await _register_agent(registry, "agent_b", HealthStatus.HEALTHY)

        runtime = StubRuntime()
        orch = _build_orchestrator(registry=registry, runtime=runtime)
        await orch.handle("hello")

        # agent_a is unavailable, so agent_b should be picked
        handler_name, _ = runtime.invoke_calls[0]
        assert handler_name == "agent_b"

    @pytest.mark.asyncio
    async def test_all_handlers_unavailable_uses_fallback(self) -> None:
        """When all handlers are unavailable, fallback is used."""
        registry = InMemoryRegistry()
        await _register_agent(registry, "agent_a", HealthStatus.UNAVAILABLE)
        await _register_agent(registry, "agent_b", HealthStatus.UNAVAILABLE)

        runtime = StubRuntime()
        orch = _build_orchestrator(registry=registry, runtime=runtime)
        await orch.handle("hello")

        handler_name, _ = runtime.invoke_calls[0]
        assert handler_name == FALLBACK_HANDLER

    @pytest.mark.asyncio
    async def test_registry_wraps_tool_manager(self) -> None:
        """With registry, unavailable tools are filtered from discovery."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.HEALTHY)
        await _register_tool(registry, "tool_y", HealthStatus.UNAVAILABLE)

        inner_tools = StubToolManager()
        orch = _build_orchestrator(registry=registry, tools=inner_tools)

        # Access the wrapped tools through the orchestrator
        tools = await orch._tools.discover(make_ctx())
        names = [t.name for t in tools]
        assert "tool_x" in names
        assert "tool_y" not in names

    @pytest.mark.asyncio
    async def test_no_registry_no_tools_is_fine(self) -> None:
        """Without registry or tools, orchestrator works normally."""
        orch = _build_orchestrator()
        resp = await orch.handle("hello")
        assert resp.text == "result"


# ===========================================================================
# N-159: Automatic invocation recording
# ===========================================================================


class TestInvocationRecording:
    """Verify that invocation stats are recorded in the registry."""

    @pytest.mark.asyncio
    async def test_success_records_stats(self) -> None:
        """Successful invocation updates stats in the registry."""
        registry = InMemoryRegistry()
        entry = await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        orch = _build_orchestrator(
            registry=registry,
            runtime=StubRuntime(status=AgentStatus.SUCCESS),
        )
        await orch.handle("hello")

        assert entry.stats.total_calls == 1
        assert entry.stats.successes == 1
        assert entry.stats.failures == 0
        assert entry.stats.avg_duration_ms > 0

    @pytest.mark.asyncio
    async def test_failure_records_stats(self) -> None:
        """Failed invocation records failure in the registry."""
        registry = InMemoryRegistry()
        entry = await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        orch = _build_orchestrator(
            registry=registry,
            runtime=StubRuntime(status=AgentStatus.ERROR),
        )
        await orch.handle("hello")

        assert entry.stats.total_calls == 1
        assert entry.stats.successes == 0
        assert entry.stats.failures == 1

    @pytest.mark.asyncio
    async def test_no_registry_no_recording(self) -> None:
        """Without registry, no stats recording happens (no crash)."""
        orch = _build_orchestrator(registry=None)
        resp = await orch.handle("hello")
        assert resp.text == "result"

    @pytest.mark.asyncio
    async def test_unregistered_handler_no_recording(self) -> None:
        """Invoking a handler not in the registry does not crash."""
        registry = InMemoryRegistry()
        # agent_a is the routed handler, but it's not registered
        orch = _build_orchestrator(registry=registry)
        resp = await orch.handle("hello")
        assert resp.text == "result"

    @pytest.mark.asyncio
    async def test_multiple_invocations_accumulate(self) -> None:
        """Multiple invocations accumulate in stats."""
        registry = InMemoryRegistry()
        entry = await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        orch = _build_orchestrator(
            registry=registry,
            runtime=StubRuntime(status=AgentStatus.SUCCESS),
        )
        await orch.handle("first")
        await orch.handle("second")
        await orch.handle("third")

        assert entry.stats.total_calls == 3
        assert entry.stats.successes == 3

    @pytest.mark.asyncio
    async def test_recording_does_not_affect_response(self) -> None:
        """Recording stats does not alter the response."""
        registry = InMemoryRegistry()
        await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        orch = _build_orchestrator(
            registry=registry,
            runtime=StubRuntime(output="hello world"),
        )
        resp = await orch.handle("hi")

        assert resp.text == "hello world"
        assert resp.channel == API_CHANNEL

    @pytest.mark.asyncio
    async def test_timeout_status_records_as_failure(self) -> None:
        """TIMEOUT status is recorded as a failure."""
        registry = InMemoryRegistry()
        entry = await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        orch = _build_orchestrator(
            registry=registry,
            runtime=StubRuntime(status=AgentStatus.TIMEOUT),
        )
        await orch.handle("slow request")

        assert entry.stats.failures == 1
        assert entry.stats.successes == 0

    @pytest.mark.asyncio
    async def test_last_invoked_at_is_set(self) -> None:
        """After invocation, last_invoked_at is set to a timestamp."""
        registry = InMemoryRegistry()
        entry = await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        assert entry.stats.last_invoked_at is None

        orch = _build_orchestrator(registry=registry)
        await orch.handle("trigger")

        assert entry.stats.last_invoked_at is not None
        assert entry.stats.last_invoked_at > 0


# ===========================================================================
# Edge cases
# ===========================================================================


class TestRegistryIntegrationEdgeCases:
    """Stress edge conditions for registry integration."""

    @pytest.mark.asyncio
    async def test_empty_registry_with_router(self) -> None:
        """Empty registry does not filter any candidates (all unregistered)."""
        registry = InMemoryRegistry()
        wrapped = RegistryAwareRouter(StubRouter(), registry)
        result = await wrapped.classify("hello", make_ctx())

        assert len(result.handlers) == 2

    @pytest.mark.asyncio
    async def test_empty_registry_with_tools(self) -> None:
        """Empty registry does not filter any tools."""
        registry = InMemoryRegistry()
        wrapped = RegistryAwareToolManager(StubToolManager(), registry)
        tools = await wrapped.discover(make_ctx())

        assert len(tools) == 2

    @pytest.mark.asyncio
    async def test_health_changes_between_calls(self) -> None:
        """If health changes between invocations, filtering reflects it."""
        registry = InMemoryRegistry()
        entry = await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        wrapped = RegistryAwareRouter(StubRouter(), registry)

        # First call — agent_a is healthy
        result1 = await wrapped.classify("hello", make_ctx())
        assert "agent_a" in [h.name for h in result1.handlers]

        # Mark agent_a as unavailable
        await registry.update("agent_a", RegistryPatch(health=HealthStatus.UNAVAILABLE))

        # Second call — agent_a is now filtered
        result2 = await wrapped.classify("hello", make_ctx())
        assert "agent_a" not in [h.name for h in result2.handlers]

    @pytest.mark.asyncio
    async def test_tool_call_after_health_change(self) -> None:
        """Tool call is rejected after health changes to unavailable."""
        registry = InMemoryRegistry()
        await _register_tool(registry, "tool_x", HealthStatus.HEALTHY)

        inner = StubToolManager()
        wrapped = RegistryAwareToolManager(inner, registry)

        # Healthy — call succeeds
        result1 = await wrapped.call("tool_x", {}, make_ctx())
        assert result1.status == ToolStatus.SUCCESS

        # Mark unavailable
        await registry.update("tool_x", RegistryPatch(health=HealthStatus.UNAVAILABLE))

        # Now rejected
        result2 = await wrapped.call("tool_x", {}, make_ctx())
        assert result2.status == ToolStatus.NOT_FOUND

    @pytest.mark.asyncio
    async def test_concurrent_invocations_accumulate(self) -> None:
        """Concurrent invocations all record stats correctly."""
        import asyncio

        registry = InMemoryRegistry()
        entry = await _register_agent(registry, "agent_a", HealthStatus.HEALTHY)

        orch = _build_orchestrator(registry=registry)
        tasks = [orch.handle(f"msg_{i}") for i in range(5)]
        await asyncio.gather(*tasks)

        assert entry.stats.total_calls == 5
        assert entry.stats.successes == 5
