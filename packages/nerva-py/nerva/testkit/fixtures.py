"""Pytest fixtures for Nerva testkit.

Provides ready-to-use fixtures for common test setups.
Import and use in your ``conftest.py`` or directly in test files.
"""

from __future__ import annotations

import pytest

from nerva.context import ExecContext
from nerva.testkit.builders import TestOrchestrator, TestOrchestratorResult


@pytest.fixture
def ctx() -> ExecContext:
    """Provide a fresh ExecContext with test defaults.

    Returns:
        ExecContext with user_id ``"test-user"`` and session_id ``"test-session"``.
    """
    return ExecContext.create(user_id="test-user", session_id="test-session")


@pytest.fixture
def test_orchestrator() -> TestOrchestratorResult:
    """Provide a fully-wired TestOrchestratorResult with spy-wrapped defaults.

    All primitives use real in-memory implementations wrapped in spies.
    Override individual primitives by building your own via ``TestOrchestrator.build()``.

    Returns:
        TestOrchestratorResult with orchestrator and spy references.
    """
    return TestOrchestrator.build()


@pytest.fixture
def spy_router(test_orchestrator: TestOrchestratorResult):
    """Provide the SpyRouter from the default test orchestrator.

    Returns:
        SpyRouter from the test orchestrator.
    """
    return test_orchestrator.router


@pytest.fixture
def spy_runtime(test_orchestrator: TestOrchestratorResult):
    """Provide the SpyRuntime from the default test orchestrator.

    Returns:
        SpyRuntime from the test orchestrator.
    """
    return test_orchestrator.runtime


@pytest.fixture
def spy_memory(test_orchestrator: TestOrchestratorResult):
    """Provide the SpyMemory from the default test orchestrator.

    Returns:
        SpyMemory from the test orchestrator.
    """
    return test_orchestrator.memory


@pytest.fixture
def spy_policy(test_orchestrator: TestOrchestratorResult):
    """Provide the SpyPolicy from the default test orchestrator.

    Returns:
        SpyPolicy from the test orchestrator.
    """
    return test_orchestrator.policy


@pytest.fixture
def spy_tools(test_orchestrator: TestOrchestratorResult):
    """Provide the SpyToolManager from the default test orchestrator.

    Returns:
        SpyToolManager from the test orchestrator.
    """
    return test_orchestrator.tools


@pytest.fixture
def spy_responder(test_orchestrator: TestOrchestratorResult):
    """Provide the SpyResponder from the default test orchestrator.

    Returns:
        SpyResponder from the test orchestrator.
    """
    return test_orchestrator.responder
