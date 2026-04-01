"""TestOrchestrator builder — wires real defaults with spy wrappers.

The builder creates an ``Orchestrator`` with spy-wrapped real implementations
as defaults, letting users override any primitive while keeping the rest wired
to lightweight, in-memory implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from nerva.memory.hot import InMemoryHotMemory
from nerva.memory.tiered import TieredMemory
from nerva.orchestrator import Orchestrator
from nerva.policy.noop import NoopPolicyEngine
from nerva.responder.passthrough import PassthroughResponder
from nerva.router.rule import RuleRouter
from nerva.runtime.inprocess import InProcessRuntime
from nerva.testkit.spies import (
    SpyMemory,
    SpyPolicy,
    SpyResponder,
    SpyRouter,
    SpyRuntime,
    SpyToolManager,
)
from nerva.tools.function import FunctionToolManager

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class TestOrchestratorResult:
    """Container holding the orchestrator and all spy references.

    Attributes:
        orchestrator: The wired Orchestrator instance.
        router: SpyRouter wrapping the real (or provided) router.
        runtime: SpyRuntime wrapping the real (or provided) runtime.
        responder: SpyResponder wrapping the real (or provided) responder.
        memory: SpyMemory wrapping the real (or provided) memory.
        policy: SpyPolicy wrapping the real (or provided) policy engine.
        tools: SpyToolManager wrapping the real (or provided) tool manager.
    """

    orchestrator: Orchestrator
    router: SpyRouter
    runtime: SpyRuntime
    responder: SpyResponder
    memory: SpyMemory
    policy: SpyPolicy
    tools: SpyToolManager

    def reset_all(self) -> None:
        """Reset all spies — clears call history and pending expectations."""
        self.router.reset()
        self.runtime.reset()
        self.responder.reset()
        self.memory.reset()
        self.policy.reset()
        self.tools.reset()

    def verify_all_expectations_consumed(self) -> None:
        """Assert that no spy has unconsumed expectations.

        Raises:
            AssertionError: If any spy has pending expectations.
        """
        self.router.verify_expectations_consumed()
        self.runtime.verify_expectations_consumed()
        self.responder.verify_expectations_consumed()
        self.memory.verify_expectations_consumed()
        self.policy.verify_expectations_consumed()
        self.tools.verify_expectations_consumed()


# ---------------------------------------------------------------------------
# Default catch-all rule for RuleRouter
# ---------------------------------------------------------------------------

CATCH_ALL_PATTERN = ".*"
CATCH_ALL_HANDLER = "default"
CATCH_ALL_INTENT = "general"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class TestOrchestrator:
    """Factory for building fully-wired test orchestrators.

    All primitives default to spy-wrapped real implementations. Provide
    overrides for any primitive — if the override is already a spy, it
    is used directly; otherwise it gets wrapped in one.
    """

    @staticmethod
    def build(
        *,
        router: SpyRouter | object | None = None,
        runtime: SpyRuntime | object | None = None,
        responder: SpyResponder | object | None = None,
        memory: SpyMemory | object | None = None,
        policy: SpyPolicy | object | None = None,
        tools: SpyToolManager | object | None = None,
        handlers: dict[str, object] | None = None,
    ) -> TestOrchestratorResult:
        """Build a test orchestrator with spy-wrapped real defaults.

        Args:
            router: IntentRouter or SpyRouter override.
            runtime: AgentRuntime or SpyRuntime override.
            responder: Responder or SpyResponder override.
            memory: Memory or SpyMemory override.
            policy: PolicyEngine or SpyPolicy override.
            tools: ToolManager or SpyToolManager override.
            handlers: Dict of handler_name -> async handler function to
                register in the default InProcessRuntime. Ignored if
                ``runtime`` is provided.

        Returns:
            TestOrchestratorResult with orchestrator and spy references.
        """
        spy_router = _ensure_spy_router(router)
        spy_runtime = _ensure_spy_runtime(runtime, handlers)
        spy_responder = _ensure_spy_responder(responder)
        spy_memory = _ensure_spy_memory(memory)
        spy_policy = _ensure_spy_policy(policy)
        spy_tools = _ensure_spy_tools(tools)

        orch = Orchestrator(
            router=spy_router,
            runtime=spy_runtime,
            responder=spy_responder,
            memory=spy_memory,
            policy=spy_policy,
            tools=spy_tools,
        )

        return TestOrchestratorResult(
            orchestrator=orch,
            router=spy_router,
            runtime=spy_runtime,
            responder=spy_responder,
            memory=spy_memory,
            policy=spy_policy,
            tools=spy_tools,
        )


# ---------------------------------------------------------------------------
# Internal helpers — wrap-or-passthrough for each primitive
# ---------------------------------------------------------------------------


def _ensure_spy_router(provided: SpyRouter | object | None) -> SpyRouter:
    """Wrap the provided router in a SpyRouter if it isn't one already.

    Args:
        provided: A router instance, SpyRouter, or None for default.

    Returns:
        A SpyRouter wrapping the appropriate implementation.
    """
    if isinstance(provided, SpyRouter):
        return provided
    if provided is not None:
        return SpyRouter(provided)  # type: ignore[arg-type]

    from nerva.router.rule import Rule

    default_router = RuleRouter(
        rules=[Rule(pattern=CATCH_ALL_PATTERN, handler=CATCH_ALL_HANDLER, intent=CATCH_ALL_INTENT)],
    )
    return SpyRouter(default_router)


def _ensure_spy_runtime(
    provided: SpyRuntime | object | None,
    handlers: dict[str, object] | None,
) -> SpyRuntime:
    """Wrap the provided runtime in a SpyRuntime if it isn't one already.

    Args:
        provided: A runtime instance, SpyRuntime, or None for default.
        handlers: Handler functions to register in the default runtime.

    Returns:
        A SpyRuntime wrapping the appropriate implementation.
    """
    if isinstance(provided, SpyRuntime):
        return provided
    if provided is not None:
        return SpyRuntime(provided)  # type: ignore[arg-type]

    default_runtime = InProcessRuntime()
    if handlers:
        for name, fn in handlers.items():
            default_runtime.register(name, fn)  # type: ignore[arg-type]
    return SpyRuntime(default_runtime)


def _ensure_spy_responder(provided: SpyResponder | object | None) -> SpyResponder:
    """Wrap the provided responder in a SpyResponder if it isn't one already.

    Args:
        provided: A responder instance, SpyResponder, or None for default.

    Returns:
        A SpyResponder wrapping the appropriate implementation.
    """
    if isinstance(provided, SpyResponder):
        return provided
    if provided is not None:
        return SpyResponder(provided)  # type: ignore[arg-type]
    return SpyResponder(PassthroughResponder())


def _ensure_spy_memory(provided: SpyMemory | object | None) -> SpyMemory:
    """Wrap the provided memory in a SpyMemory if it isn't one already.

    Args:
        provided: A memory instance, SpyMemory, or None for default.

    Returns:
        A SpyMemory wrapping the appropriate implementation.
    """
    if isinstance(provided, SpyMemory):
        return provided
    if provided is not None:
        return SpyMemory(provided)  # type: ignore[arg-type]
    return SpyMemory(TieredMemory(hot=InMemoryHotMemory()))


def _ensure_spy_policy(provided: SpyPolicy | object | None) -> SpyPolicy:
    """Wrap the provided policy in a SpyPolicy if it isn't one already.

    Args:
        provided: A policy instance, SpyPolicy, or None for default.

    Returns:
        A SpyPolicy wrapping the appropriate implementation.
    """
    if isinstance(provided, SpyPolicy):
        return provided
    if provided is not None:
        return SpyPolicy(provided)  # type: ignore[arg-type]
    return SpyPolicy(NoopPolicyEngine())


def _ensure_spy_tools(provided: SpyToolManager | object | None) -> SpyToolManager:
    """Wrap the provided tool manager in a SpyToolManager if it isn't one already.

    Args:
        provided: A tool manager instance, SpyToolManager, or None for default.

    Returns:
        A SpyToolManager wrapping the appropriate implementation.
    """
    if isinstance(provided, SpyToolManager):
        return provided
    if provided is not None:
        return SpyToolManager(provided)  # type: ignore[arg-type]
    return SpyToolManager(FunctionToolManager())
