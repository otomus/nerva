"""Tests for policy decorator and adaptive policy engine.

Covers:
- N-189a: @agent decorator, registry, resolve_policy
- N-189b: AdaptivePolicyEngine condition checks (timeout, throttle, pause)
- N-189c: AdaptivePolicyEngine.evaluate integration with base engine
- N-189d: Edge cases — boundary values, negative cost, zero factors, special names
"""

from __future__ import annotations

import pytest

from nerva.context import ExecContext, TokenUsage
from nerva.policy import ALLOW, DENY_NO_REASON, PolicyAction, PolicyDecision, PolicyEngine
from nerva.policy.adaptive import (
    COST_DISABLED,
    REASON_BUDGET_EXCEEDED,
    REASON_THROTTLED,
    AdaptivePolicyConfig,
    AdaptivePolicyEngine,
)
from nerva.policy.decorator import (
    AgentPolicyConfig,
    agent,
    clear_registry,
    get_agent_policy,
    resolve_policy,
)

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubPolicyEngine:
    """Minimal PolicyEngine that returns a configurable decision."""

    def __init__(self, decision: PolicyDecision = ALLOW) -> None:
        self.decision = decision
        self.recorded: list[tuple[PolicyAction, PolicyDecision, ExecContext]] = []

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        return self.decision

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        self.recorded.append((action, decision, ctx))


ACTION = PolicyAction(kind="invoke_agent", subject="user-1", target="deploy_agent")


def _ctx_with_cost(cost_usd: float, **kwargs) -> ExecContext:
    """Build an ExecContext with a specific accumulated cost."""
    ctx = make_ctx(**kwargs)
    ctx.token_usage = TokenUsage(cost_usd=cost_usd)
    return ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure the decorator registry is empty before and after every test."""
    clear_registry()
    yield
    clear_registry()


# ===========================================================================
# N-189a — Decorator & Registry
# ===========================================================================


class TestAgentDecorator:
    """Tests for the @agent decorator and registry functions."""

    def test_registers_policy_config(self) -> None:
        """@agent stores an AgentPolicyConfig keyed by name."""

        @agent(name="my_agent", policy={"requires_approval": True, "timeout_seconds": 60})
        class MyAgent:
            pass

        config = get_agent_policy("my_agent")
        assert config is not None
        assert config.requires_approval is True
        assert config.timeout_seconds == 60
        # Unset fields remain None
        assert config.max_tool_calls is None
        assert config.max_cost_usd is None
        assert config.approvers is None

    def test_get_agent_policy_returns_none_for_unregistered(self) -> None:
        assert get_agent_policy("nonexistent_agent") is None

    def test_empty_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            @agent(name="")
            class Bad:
                pass

    def test_none_name_raises_value_error(self) -> None:
        """None is falsy, should also raise."""
        with pytest.raises(ValueError, match="non-empty"):
            @agent(name=None)  # type: ignore[arg-type]
            class Bad:
                pass

    def test_policy_none_registers_empty_config(self) -> None:
        @agent(name="bare", policy=None)
        def bare_fn():
            pass

        config = get_agent_policy("bare")
        assert config is not None
        assert config == AgentPolicyConfig()

    def test_unknown_keys_ignored(self) -> None:
        """Keys not in _OVERRIDE_FIELDS are silently dropped."""

        @agent(name="extra", policy={"bogus_key": 999, "timeout_seconds": 10})
        class Extra:
            pass

        config = get_agent_policy("extra")
        assert config is not None
        assert config.timeout_seconds == 10
        assert not hasattr(config, "bogus_key")

    def test_clear_registry_resets_state(self) -> None:
        @agent(name="temp")
        class Temp:
            pass

        assert get_agent_policy("temp") is not None
        clear_registry()
        assert get_agent_policy("temp") is None

    def test_decorator_returns_class_unchanged(self) -> None:
        @agent(name="identity")
        class Original:
            x = 42

        assert Original.x == 42

    def test_decorator_returns_function_unchanged(self) -> None:
        @agent(name="fn_identity")
        def my_fn(a: int) -> int:
            return a + 1

        assert my_fn(5) == 6

    def test_very_long_name(self) -> None:
        long_name = "a" * 10_000

        @agent(name=long_name, policy={"max_tool_calls": 3})
        class Long:
            pass

        assert get_agent_policy(long_name) is not None
        assert get_agent_policy(long_name).max_tool_calls == 3

    def test_special_characters_in_name(self) -> None:
        weird = "agent/with spaces\nand\ttabs!@#$%^&*()"

        @agent(name=weird, policy={"requires_approval": True})
        class Weird:
            pass

        config = get_agent_policy(weird)
        assert config is not None
        assert config.requires_approval is True

    def test_overwrite_same_name(self) -> None:
        """Re-registering the same name replaces the previous config."""

        @agent(name="dup", policy={"timeout_seconds": 10})
        class First:
            pass

        @agent(name="dup", policy={"timeout_seconds": 99})
        class Second:
            pass

        assert get_agent_policy("dup").timeout_seconds == 99


class TestResolvePolicy:
    """Tests for resolve_policy — YAML + decorator merge."""

    def test_decorator_wins_over_yaml(self) -> None:
        yaml_cfg = {"timeout_seconds": 30, "max_tool_calls": 5}

        @agent(name="merge_agent", policy={"timeout_seconds": 120})
        class Merge:
            pass

        merged = resolve_policy(yaml_cfg, "merge_agent")
        assert merged["timeout_seconds"] == 120  # decorator wins
        assert merged["max_tool_calls"] == 5  # YAML preserved

    def test_no_decorator_returns_yaml_copy(self) -> None:
        yaml_cfg = {"timeout_seconds": 30, "custom_key": "keep"}
        merged = resolve_policy(yaml_cfg, "no_such_agent")
        assert merged == yaml_cfg
        # Must be a copy, not the same dict
        assert merged is not yaml_cfg

    def test_empty_yaml_with_decorator(self) -> None:
        @agent(name="from_scratch", policy={"requires_approval": True})
        class Scratch:
            pass

        merged = resolve_policy({}, "from_scratch")
        assert merged["requires_approval"] is True

    def test_yaml_extra_keys_preserved(self) -> None:
        """Keys outside _OVERRIDE_FIELDS survive the merge."""
        yaml_cfg = {"timeout_seconds": 10, "rate_limit": 100}

        @agent(name="extra_yaml", policy={"timeout_seconds": 60})
        class X:
            pass

        merged = resolve_policy(yaml_cfg, "extra_yaml")
        assert merged["rate_limit"] == 100
        assert merged["timeout_seconds"] == 60

    def test_none_override_does_not_clobber_yaml(self) -> None:
        """Only non-None decorator values replace YAML values."""
        yaml_cfg = {"timeout_seconds": 30, "max_tool_calls": 5}

        @agent(name="partial", policy={"requires_approval": True})
        class Partial:
            pass

        merged = resolve_policy(yaml_cfg, "partial")
        assert merged["timeout_seconds"] == 30  # unchanged
        assert merged["max_tool_calls"] == 5  # unchanged
        assert merged["requires_approval"] is True  # added


# ===========================================================================
# N-189b — AdaptivePolicyEngine condition checks
# ===========================================================================


class TestShouldExtendTimeout:
    """Tests for should_extend_timeout."""

    def test_returns_true_when_tag_present(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(extend_timeout_on=frozenset({"long_running"})),
        )
        ctx = make_ctx(metadata={"long_running": "true"})
        assert engine.should_extend_timeout(ctx) is True

    def test_returns_false_when_no_matching_tags(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(extend_timeout_on=frozenset({"long_running"})),
        )
        ctx = make_ctx(metadata={"unrelated": "value"})
        assert engine.should_extend_timeout(ctx) is False

    def test_returns_false_when_no_tags_configured(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(extend_timeout_on=frozenset()),
        )
        ctx = make_ctx(metadata={"long_running": "true"})
        assert engine.should_extend_timeout(ctx) is False

    def test_returns_false_when_metadata_empty(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(extend_timeout_on=frozenset({"tag"})),
        )
        ctx = make_ctx()
        assert engine.should_extend_timeout(ctx) is False


class TestGetExtendedTimeout:
    """Tests for get_extended_timeout."""

    def test_returns_base_times_factor(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(base_timeout_seconds=30.0, timeout_extension_factor=2.0),
        )
        assert engine.get_extended_timeout() == 60.0

    def test_zero_extension_factor(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(base_timeout_seconds=30.0, timeout_extension_factor=0.0),
        )
        assert engine.get_extended_timeout() == 0.0

    def test_fractional_factor(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(base_timeout_seconds=100.0, timeout_extension_factor=1.5),
        )
        assert engine.get_extended_timeout() == 150.0


class TestShouldThrottle:
    """Tests for should_throttle."""

    def test_true_when_cost_exceeds_threshold(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(throttle_after_cost=1.0),
        )
        ctx = _ctx_with_cost(1.5)
        assert engine.should_throttle(ctx) is True

    def test_false_when_below_threshold(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(throttle_after_cost=1.0),
        )
        ctx = _ctx_with_cost(0.5)
        assert engine.should_throttle(ctx) is False

    def test_false_when_threshold_disabled(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(throttle_after_cost=COST_DISABLED),
        )
        ctx = _ctx_with_cost(999.0)
        assert engine.should_throttle(ctx) is False

    def test_true_when_cost_exactly_at_threshold(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(throttle_after_cost=1.0),
        )
        ctx = _ctx_with_cost(1.0)
        assert engine.should_throttle(ctx) is True

    def test_negative_cost(self) -> None:
        """Negative cost should never trigger throttle."""
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(throttle_after_cost=1.0),
        )
        ctx = _ctx_with_cost(-5.0)
        assert engine.should_throttle(ctx) is False


class TestShouldPause:
    """Tests for should_pause."""

    def test_true_when_cost_exceeds_threshold(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(pause_after_cost=2.0),
        )
        ctx = _ctx_with_cost(2.5)
        assert engine.should_pause(ctx) is True

    def test_false_when_below_threshold(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(pause_after_cost=2.0),
        )
        ctx = _ctx_with_cost(1.0)
        assert engine.should_pause(ctx) is False

    def test_false_when_disabled(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(pause_after_cost=COST_DISABLED),
        )
        ctx = _ctx_with_cost(999.0)
        assert engine.should_pause(ctx) is False

    def test_true_at_exact_threshold(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(pause_after_cost=2.0),
        )
        ctx = _ctx_with_cost(2.0)
        assert engine.should_pause(ctx) is True


# ===========================================================================
# N-189c — AdaptivePolicyEngine.evaluate (integration with base)
# ===========================================================================


class TestAdaptiveEvaluate:
    """Tests for AdaptivePolicyEngine.evaluate — layered decisions."""

    @pytest.mark.asyncio
    async def test_base_allows_no_adaptive_triggers(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(decision=ALLOW),
            config=AdaptivePolicyConfig(),
        )
        ctx = make_ctx()
        decision = await engine.evaluate(ACTION, ctx)
        assert decision.allowed is True
        assert decision.reason is None

    @pytest.mark.asyncio
    async def test_base_denies_overrides_everything(self) -> None:
        """A base DENY is never overridden, even if adaptive would allow."""
        denial = PolicyDecision(allowed=False, reason="base_says_no")
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(decision=denial),
            config=AdaptivePolicyConfig(
                throttle_after_cost=0.01,  # would throttle
                pause_after_cost=100.0,
            ),
        )
        ctx = _ctx_with_cost(50.0)
        decision = await engine.evaluate(ACTION, ctx)
        assert decision.allowed is False
        assert decision.reason == "base_says_no"

    @pytest.mark.asyncio
    async def test_pause_triggered_returns_deny(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(decision=ALLOW),
            config=AdaptivePolicyConfig(pause_after_cost=1.0),
        )
        ctx = _ctx_with_cost(1.5)
        decision = await engine.evaluate(ACTION, ctx)
        assert decision.allowed is False
        assert decision.reason == REASON_BUDGET_EXCEEDED
        assert decision.budget_remaining == 0.0

    @pytest.mark.asyncio
    async def test_throttle_triggered_returns_allow_with_advisory(self) -> None:
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(decision=ALLOW),
            config=AdaptivePolicyConfig(
                throttle_after_cost=1.0,
                pause_after_cost=5.0,
            ),
        )
        ctx = _ctx_with_cost(2.0)
        decision = await engine.evaluate(ACTION, ctx)
        assert decision.allowed is True
        assert decision.reason == REASON_THROTTLED
        assert decision.budget_remaining == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_throttle_without_pause_threshold(self) -> None:
        """When no pause threshold, budget_remaining is None."""
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(decision=ALLOW),
            config=AdaptivePolicyConfig(throttle_after_cost=1.0),
        )
        ctx = _ctx_with_cost(2.0)
        decision = await engine.evaluate(ACTION, ctx)
        assert decision.allowed is True
        assert decision.reason == REASON_THROTTLED
        assert decision.budget_remaining is None

    @pytest.mark.asyncio
    async def test_pause_takes_priority_over_throttle(self) -> None:
        """When both thresholds are exceeded, pause (deny) wins over throttle (allow)."""
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(decision=ALLOW),
            config=AdaptivePolicyConfig(
                throttle_after_cost=1.0,
                pause_after_cost=2.0,
            ),
        )
        ctx = _ctx_with_cost(3.0)
        decision = await engine.evaluate(ACTION, ctx)
        assert decision.allowed is False
        assert decision.reason == REASON_BUDGET_EXCEEDED


# ===========================================================================
# N-189d — record delegation
# ===========================================================================


class TestAdaptiveRecord:
    """Tests for AdaptivePolicyEngine.record — delegation to base."""

    @pytest.mark.asyncio
    async def test_record_delegates_to_base(self) -> None:
        stub = StubPolicyEngine()
        engine = AdaptivePolicyEngine(
            base=stub,
            config=AdaptivePolicyConfig(),
        )
        ctx = make_ctx()
        await engine.record(ACTION, ALLOW, ctx)

        assert len(stub.recorded) == 1
        recorded_action, recorded_decision, recorded_ctx = stub.recorded[0]
        assert recorded_action is ACTION
        assert recorded_decision is ALLOW
        assert recorded_ctx is ctx

    @pytest.mark.asyncio
    async def test_record_multiple_calls(self) -> None:
        stub = StubPolicyEngine()
        engine = AdaptivePolicyEngine(base=stub, config=AdaptivePolicyConfig())
        ctx = make_ctx()
        for _ in range(5):
            await engine.record(ACTION, ALLOW, ctx)
        assert len(stub.recorded) == 5


# ===========================================================================
# Edge cases — properties and config
# ===========================================================================


class TestAdaptiveEngineProperties:
    """Tests for config/base properties and config edge cases."""

    def test_config_property_returns_config(self) -> None:
        cfg = AdaptivePolicyConfig(base_timeout_seconds=99.0)
        engine = AdaptivePolicyEngine(base=StubPolicyEngine(), config=cfg)
        assert engine.config is cfg

    def test_base_property_returns_base(self) -> None:
        stub = StubPolicyEngine()
        engine = AdaptivePolicyEngine(base=stub, config=AdaptivePolicyConfig())
        assert engine.base is stub

    def test_config_is_frozen(self) -> None:
        cfg = AdaptivePolicyConfig()
        with pytest.raises(AttributeError):
            cfg.base_timeout_seconds = 999  # type: ignore[misc]

    def test_negative_threshold_acts_as_disabled(self) -> None:
        """Negative thresholds are <= COST_DISABLED, so treated as disabled."""
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(
                throttle_after_cost=-1.0,
                pause_after_cost=-1.0,
            ),
        )
        ctx = _ctx_with_cost(999.0)
        assert engine.should_throttle(ctx) is False
        assert engine.should_pause(ctx) is False

    def test_budget_remaining_clamped_to_zero(self) -> None:
        """When cost far exceeds pause threshold, remaining is 0, not negative."""
        engine = AdaptivePolicyEngine(
            base=StubPolicyEngine(),
            config=AdaptivePolicyConfig(
                throttle_after_cost=1.0,
                pause_after_cost=2.0,
            ),
        )
        ctx = _ctx_with_cost(100.0)
        # Directly test the private method
        remaining = engine._compute_budget_remaining(ctx)
        assert remaining == 0.0
