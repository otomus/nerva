"""Nerva testkit — spy wrappers, builders, assertions, and fixtures for testing.

The testkit provides reusable test infrastructure for code built on Nerva
primitives. Instead of hand-rolling mocks, import spy-wrapped real
implementations that record every call and support expectation-setting.

Usage::

    from nerva.testkit import TestOrchestrator, assert_routed_to

    result = TestOrchestrator.build()
    result.runtime.expect_llm_response("Hello!")
    response = await result.orchestrator.handle("hi")
    assert_routed_to(result.router, "default")
"""

# -- Spies -----------------------------------------------------------------
from nerva.testkit.spies import (
    ClassifyCall,
    DelegateCall,
    DiscoverToolsCall,
    EvaluateCall,
    FormatCall,
    InvokeCall,
    PolicyRecordCall,
    RecallCall,
    SpyMemory,
    SpyPolicy,
    SpyResponder,
    SpyRouter,
    SpyRuntime,
    SpyToolManager,
    StoreCall,
    ToolCallRecord,
)

# -- Builders --------------------------------------------------------------
from nerva.testkit.builders import (
    TestOrchestrator,
    TestOrchestratorResult,
)

# -- Assertions ------------------------------------------------------------
from nerva.testkit.assertions import (
    assert_handler_invoked,
    assert_memory_recalled,
    assert_memory_stored,
    assert_no_unconsumed_expectations,
    assert_pipeline_order,
    assert_policy_allowed,
    assert_policy_denied,
    assert_routed_to,
    assert_tool_called,
)

# -- Boundaries ------------------------------------------------------------
from nerva.testkit.boundaries import (
    AllowAllPolicy,
    DenyAllPolicy,
    StubLLMHandler,
)

# -- MCP -------------------------------------------------------------------
from nerva.testkit.mcp import MCPTestHarness

__all__ = [
    # Spies
    "SpyRouter",
    "SpyRuntime",
    "SpyResponder",
    "SpyMemory",
    "SpyPolicy",
    "SpyToolManager",
    # Call records
    "ClassifyCall",
    "InvokeCall",
    "DelegateCall",
    "FormatCall",
    "RecallCall",
    "StoreCall",
    "EvaluateCall",
    "PolicyRecordCall",
    "DiscoverToolsCall",
    "ToolCallRecord",
    # Builders
    "TestOrchestrator",
    "TestOrchestratorResult",
    # Assertions
    "assert_routed_to",
    "assert_handler_invoked",
    "assert_policy_allowed",
    "assert_policy_denied",
    "assert_memory_stored",
    "assert_memory_recalled",
    "assert_tool_called",
    "assert_no_unconsumed_expectations",
    "assert_pipeline_order",
    # Boundaries
    "StubLLMHandler",
    "DenyAllPolicy",
    "AllowAllPolicy",
    # MCP
    "MCPTestHarness",
]
