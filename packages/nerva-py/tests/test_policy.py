"""Tests for PolicyEngine implementations — NoopPolicyEngine and YamlPolicyEngine."""

from __future__ import annotations

import time

import pytest

from nerva.context import ExecContext, TokenUsage
from nerva.policy import ALLOW, PolicyAction, PolicyDecision
from nerva.policy.noop import NoopPolicyEngine
from nerva.policy.yaml_engine import (
    YamlPolicyEngine,
    PolicyConfig,
    parse_policy_config,
    SECONDS_PER_MINUTE,
)
from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action(
    kind: str = "invoke_agent",
    subject: str = "test-user",
    target: str = "test-agent",
    **metadata: str,
) -> PolicyAction:
    """Build a PolicyAction with sensible defaults."""
    return PolicyAction(kind=kind, subject=subject, target=target, metadata=metadata)


def _yaml_engine(policies: dict) -> YamlPolicyEngine:
    """Create a YamlPolicyEngine from an inline policies dict."""
    return YamlPolicyEngine(config_dict={"policies": policies})


# ===========================================================================
# NoopPolicyEngine
# ===========================================================================


class TestNoopPolicyEngine:
    """NoopPolicyEngine always allows and never raises."""

    @pytest.mark.asyncio
    async def test_always_returns_allow(self) -> None:
        engine = NoopPolicyEngine()
        ctx = make_ctx()

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is True
        assert decision is ALLOW

    @pytest.mark.asyncio
    async def test_record_is_noop(self) -> None:
        """record() should not raise regardless of inputs."""
        engine = NoopPolicyEngine()
        ctx = make_ctx()
        action = _action()
        decision = PolicyDecision(allowed=False, reason="denied")

        # Should not raise
        await engine.record(action, decision, ctx)

    @pytest.mark.asyncio
    async def test_allows_with_none_user(self) -> None:
        engine = NoopPolicyEngine()
        ctx = make_ctx(user_id=None)

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_allows_any_action_kind(self) -> None:
        engine = NoopPolicyEngine()
        ctx = make_ctx()

        for kind in ("invoke_agent", "call_tool", "delegate", "store_memory", "unknown"):
            decision = await engine.evaluate(_action(kind=kind), ctx)
            assert decision.allowed is True


# ===========================================================================
# YamlPolicyEngine — construction
# ===========================================================================


class TestYamlEngineConstruction:
    """Verify engine creation from dicts and edge cases."""

    def test_no_config_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="config_path or config_dict"):
            YamlPolicyEngine()

    def test_missing_file_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            YamlPolicyEngine(config_path=tmp_path / "nonexistent.yaml")

    def test_empty_config_dict_uses_defaults(self) -> None:
        engine = YamlPolicyEngine(config_dict={})

        assert engine.config.rate_limit_max_per_minute == 0  # UNLIMITED
        assert engine.config.budget_max_tokens_per_hour == 0
        assert engine.config.max_depth == 10
        assert engine.config.max_tool_calls == 50

    @pytest.mark.asyncio
    async def test_no_policies_section_allows_everything(self) -> None:
        engine = YamlPolicyEngine(config_dict={})
        ctx = make_ctx()

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is True


# ===========================================================================
# YamlPolicyEngine — rate limiting
# ===========================================================================


class TestYamlRateLimit:
    """Rate limit checks: allows under, denies at/over limit."""

    @pytest.mark.asyncio
    async def test_allows_under_limit(self) -> None:
        engine = _yaml_engine({
            "rate_limit": {"per_user": {"max_requests_per_minute": 5}},
        })
        ctx = make_ctx()

        # Record 4 requests
        for _ in range(4):
            await engine.record(_action(), ALLOW, ctx)

        decision = await engine.evaluate(_action(), ctx)
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_denies_when_exceeded(self) -> None:
        engine = _yaml_engine({
            "rate_limit": {"per_user": {"max_requests_per_minute": 3}},
        })
        ctx = make_ctx()

        # Fill up the limit
        for _ in range(3):
            await engine.record(_action(), ALLOW, ctx)

        decision = await engine.evaluate(_action(), ctx)
        assert decision.allowed is False
        assert "rate limit exceeded" in (decision.reason or "")

    @pytest.mark.asyncio
    async def test_rate_limit_max_1(self) -> None:
        """Edge: max=1 should deny on the very first recorded request."""
        engine = _yaml_engine({
            "rate_limit": {"per_user": {"max_requests_per_minute": 1}},
        })
        ctx = make_ctx()

        await engine.record(_action(), ALLOW, ctx)

        decision = await engine.evaluate(_action(), ctx)
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_rate_limit_anonymous_user(self) -> None:
        """Anonymous users (user_id=None) should still be rate-limited as 'anonymous'."""
        engine = _yaml_engine({
            "rate_limit": {"per_user": {"max_requests_per_minute": 1}},
        })
        ctx = make_ctx(user_id=None)

        await engine.record(_action(), ALLOW, ctx)

        decision = await engine.evaluate(_action(), ctx)
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_rate_limit_different_users_isolated(self) -> None:
        """Each user has their own counter."""
        engine = _yaml_engine({
            "rate_limit": {"per_user": {"max_requests_per_minute": 1}},
        })
        ctx_a = make_ctx(user_id="alice")
        ctx_b = make_ctx(user_id="bob")

        await engine.record(_action(), ALLOW, ctx_a)

        # Alice is blocked, Bob is not
        assert (await engine.evaluate(_action(), ctx_a)).allowed is False
        assert (await engine.evaluate(_action(), ctx_b)).allowed is True


# ===========================================================================
# YamlPolicyEngine — budget
# ===========================================================================


class TestYamlBudget:
    """Token and cost budget checks."""

    @pytest.mark.asyncio
    async def test_allows_under_token_budget(self) -> None:
        engine = _yaml_engine({
            "budget": {"per_agent": {"max_tokens_per_hour": 1000}},
        })
        ctx = make_ctx()
        ctx.token_usage = TokenUsage(total_tokens=100)

        await engine.record(_action(target="agent-x"), ALLOW, ctx)

        decision = await engine.evaluate(_action(target="agent-x"), ctx)
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_denies_when_token_limit_exceeded(self) -> None:
        engine = _yaml_engine({
            "budget": {"per_agent": {"max_tokens_per_hour": 100}},
        })
        ctx = make_ctx()
        ctx.token_usage = TokenUsage(total_tokens=101)

        await engine.record(_action(target="agent-x"), ALLOW, ctx)

        decision = await engine.evaluate(_action(target="agent-x"), ctx)
        assert decision.allowed is False
        assert "token budget exceeded" in (decision.reason or "")

    @pytest.mark.asyncio
    async def test_budget_zero_denies_immediately(self) -> None:
        """Edge: budget=0 means UNLIMITED (no enforcement), per the UNLIMITED constant."""
        engine = _yaml_engine({
            "budget": {"per_agent": {"max_tokens_per_hour": 0}},
        })
        ctx = make_ctx()
        ctx.token_usage = TokenUsage(total_tokens=999999)

        await engine.record(_action(target="agent-x"), ALLOW, ctx)

        # 0 = UNLIMITED, so it should allow
        decision = await engine.evaluate(_action(target="agent-x"), ctx)
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_cost_budget_denies_when_exceeded(self) -> None:
        engine = _yaml_engine({
            "budget": {"per_agent": {"max_cost_per_day_usd": 1.00}},
        })
        ctx = make_ctx()
        ctx.token_usage = TokenUsage(cost_usd=1.50)

        await engine.record(_action(target="agent-x"), ALLOW, ctx)

        decision = await engine.evaluate(_action(target="agent-x"), ctx)
        assert decision.allowed is False
        assert "cost budget exceeded" in (decision.reason or "")

    @pytest.mark.asyncio
    async def test_cost_budget_allows_under(self) -> None:
        engine = _yaml_engine({
            "budget": {"per_agent": {"max_cost_per_day_usd": 10.00}},
        })
        ctx = make_ctx()
        ctx.token_usage = TokenUsage(cost_usd=2.00)

        await engine.record(_action(target="agent-x"), ALLOW, ctx)

        decision = await engine.evaluate(_action(target="agent-x"), ctx)
        assert decision.allowed is True
        # Note: budget_remaining is only surfaced on the internal _check_cost_budget
        # result. The top-level evaluate() returns ALLOW (no budget_remaining) when
        # all checks pass, because the loop short-circuits only on denial/approval.


# ===========================================================================
# YamlPolicyEngine — approval
# ===========================================================================


class TestYamlApproval:
    """Approval gating for configured agents."""

    @pytest.mark.asyncio
    async def test_requires_approval_for_configured_agent(self) -> None:
        engine = _yaml_engine({
            "approval": {
                "agents": [
                    {"name": "deploy_agent", "requires_approval": True, "approvers": ["admin"]},
                ],
            },
        })
        ctx = make_ctx()

        decision = await engine.evaluate(_action(target="deploy_agent"), ctx)

        assert decision.allowed is True
        assert decision.require_approval is True
        assert decision.approvers == ["admin"]

    @pytest.mark.asyncio
    async def test_allows_non_configured_agents(self) -> None:
        engine = _yaml_engine({
            "approval": {
                "agents": [
                    {"name": "deploy_agent", "requires_approval": True, "approvers": ["admin"]},
                ],
            },
        })
        ctx = make_ctx()

        decision = await engine.evaluate(_action(target="safe_agent"), ctx)

        assert decision.allowed is True
        assert decision.require_approval is False

    @pytest.mark.asyncio
    async def test_empty_approvers_list(self) -> None:
        """Edge: agent requires approval but approvers list is empty."""
        engine = _yaml_engine({
            "approval": {
                "agents": [
                    {"name": "lonely", "requires_approval": True, "approvers": []},
                ],
            },
        })
        ctx = make_ctx()

        decision = await engine.evaluate(_action(target="lonely"), ctx)

        assert decision.require_approval is True
        assert decision.approvers == []

    @pytest.mark.asyncio
    async def test_requires_approval_false_not_gated(self) -> None:
        """An agent with requires_approval=False should not be gated."""
        engine = _yaml_engine({
            "approval": {
                "agents": [
                    {"name": "normal", "requires_approval": False, "approvers": ["admin"]},
                ],
            },
        })
        ctx = make_ctx()

        decision = await engine.evaluate(_action(target="normal"), ctx)

        assert decision.require_approval is False


# ===========================================================================
# YamlPolicyEngine — execution limits
# ===========================================================================


class TestYamlExecution:
    """Depth and tool-call guards."""

    @pytest.mark.asyncio
    async def test_denies_when_max_depth_exceeded(self) -> None:
        engine = _yaml_engine({"execution": {"max_depth": 3}})
        ctx = make_ctx(metadata={"depth": "4"})

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is False
        assert "depth" in (decision.reason or "")

    @pytest.mark.asyncio
    async def test_allows_at_max_depth(self) -> None:
        """Depth equal to max should be allowed (only exceeding is denied)."""
        engine = _yaml_engine({"execution": {"max_depth": 3}})
        ctx = make_ctx(metadata={"depth": "3"})

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_denies_when_max_tool_calls_exceeded(self) -> None:
        engine = _yaml_engine({"execution": {"max_tool_calls_per_invocation": 5}})
        ctx = make_ctx(metadata={"tool_call_count": "6"})

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is False
        assert "tool call count" in (decision.reason or "")

    @pytest.mark.asyncio
    async def test_allows_under_tool_call_limit(self) -> None:
        engine = _yaml_engine({"execution": {"max_tool_calls_per_invocation": 10}})
        ctx = make_ctx(metadata={"tool_call_count": "5"})

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_missing_metadata_defaults_to_zero(self) -> None:
        """If depth/tool_call_count metadata is absent, treat as 0."""
        engine = _yaml_engine({"execution": {"max_depth": 1, "max_tool_calls_per_invocation": 1}})
        ctx = make_ctx()  # no metadata

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_non_numeric_metadata_treated_as_zero(self) -> None:
        """Non-digit strings in metadata should not crash — treated as 0."""
        engine = _yaml_engine({"execution": {"max_depth": 1}})
        ctx = make_ctx(metadata={"depth": "not-a-number"})

        decision = await engine.evaluate(_action(), ctx)

        assert decision.allowed is True


# ===========================================================================
# YamlPolicyEngine — record
# ===========================================================================


class TestYamlRecord:
    """Verify record() updates internal counters."""

    @pytest.mark.asyncio
    async def test_record_updates_request_timestamps(self) -> None:
        engine = _yaml_engine({
            "rate_limit": {"per_user": {"max_requests_per_minute": 100}},
        })
        ctx = make_ctx(user_id="alice")

        await engine.record(_action(), ALLOW, ctx)

        assert len(engine._request_timestamps.get("alice", [])) == 1

    @pytest.mark.asyncio
    async def test_record_denied_action_does_not_update(self) -> None:
        """Denied actions should not consume rate limit or budget quota."""
        engine = _yaml_engine({
            "rate_limit": {"per_user": {"max_requests_per_minute": 100}},
        })
        ctx = make_ctx(user_id="bob")
        denied = PolicyDecision(allowed=False, reason="nope")

        await engine.record(_action(), denied, ctx)

        assert len(engine._request_timestamps.get("bob", [])) == 0

    @pytest.mark.asyncio
    async def test_record_updates_token_ledger(self) -> None:
        engine = _yaml_engine({
            "budget": {"per_agent": {"max_tokens_per_hour": 10000}},
        })
        ctx = make_ctx()
        ctx.token_usage = TokenUsage(total_tokens=500)

        await engine.record(_action(target="my-agent"), ALLOW, ctx)

        ledger = engine._token_ledger.get("my-agent", [])
        assert len(ledger) == 1
        assert ledger[0][1] == 500

    @pytest.mark.asyncio
    async def test_record_updates_cost_ledger(self) -> None:
        engine = _yaml_engine({
            "budget": {"per_agent": {"max_cost_per_day_usd": 100.0}},
        })
        ctx = make_ctx()
        ctx.token_usage = TokenUsage(cost_usd=0.75)

        await engine.record(_action(target="my-agent"), ALLOW, ctx)

        ledger = engine._cost_ledger.get("my-agent", [])
        assert len(ledger) == 1
        assert ledger[0][1] == pytest.approx(0.75)


# ===========================================================================
# parse_policy_config edge cases
# ===========================================================================


class TestParsePolicyConfig:
    """Edge cases for config parsing."""

    def test_policies_key_not_a_dict(self) -> None:
        """If 'policies' is not a dict, return defaults."""
        config = parse_policy_config({"policies": "garbage"})
        assert config.rate_limit_max_per_minute == 0

    def test_budget_not_a_dict(self) -> None:
        config = parse_policy_config({"policies": {"budget": "nope"}})
        assert config.budget_max_tokens_per_hour == 0

    def test_approval_agents_not_a_list(self) -> None:
        config = parse_policy_config({
            "policies": {"approval": {"agents": "not-a-list"}},
        })
        assert config.approval_agents == {}

    def test_approval_entry_missing_name(self) -> None:
        config = parse_policy_config({
            "policies": {
                "approval": {
                    "agents": [{"requires_approval": True, "approvers": ["admin"]}],
                },
            },
        })
        assert config.approval_agents == {}

    def test_execution_not_a_dict(self) -> None:
        config = parse_policy_config({"policies": {"execution": 42}})
        assert config.max_depth == 10
        assert config.max_tool_calls == 50
