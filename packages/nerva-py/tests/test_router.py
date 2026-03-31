"""Tests for RuleRouter and IntentResult (N-171 partial)."""

from __future__ import annotations

import re

import pytest

from nerva.router import HandlerCandidate, IntentResult
from nerva.router.rule import Rule, RuleRouter
from tests.conftest import make_ctx


# ===================================================================
# RuleRouter matching behaviour
# ===================================================================


class TestRuleRouterMatching:
    """Rule-based routing: first match wins, fallback logic."""

    @pytest.mark.asyncio
    async def test_matching_rule_returns_correct_handler(self):
        rules = [Rule(pattern=r"hello", handler="greeter", intent="greet")]
        router = RuleRouter(rules)
        result = await router.classify("hello world", make_ctx())
        assert result.intent == "greet"
        assert result.best_handler is not None
        assert result.best_handler.name == "greeter"

    @pytest.mark.asyncio
    async def test_first_match_wins(self):
        rules = [
            Rule(pattern=r"hello", handler="first", intent="greet"),
            Rule(pattern=r"hello", handler="second", intent="greet2"),
        ]
        router = RuleRouter(rules)
        result = await router.classify("hello", make_ctx())
        assert result.best_handler is not None
        assert result.best_handler.name == "first"

    @pytest.mark.asyncio
    async def test_no_match_with_default_handler(self):
        rules = [Rule(pattern=r"^xyz$", handler="nope", intent="x")]
        router = RuleRouter(rules, default_handler="fallback")
        result = await router.classify("no match here", make_ctx())
        assert result.intent == "default"
        assert result.best_handler is not None
        assert result.best_handler.name == "fallback"
        assert result.confidence == 0.5

    @pytest.mark.asyncio
    async def test_no_match_no_default_returns_empty(self):
        rules = [Rule(pattern=r"^xyz$", handler="nope", intent="x")]
        router = RuleRouter(rules)
        result = await router.classify("no match here", make_ctx())
        assert result.intent == "unknown"
        assert result.best_handler is None
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self):
        rules = [Rule(pattern=r"HELLO", handler="greeter", intent="greet")]
        router = RuleRouter(rules)
        result = await router.classify("hello", make_ctx())
        assert result.best_handler is not None
        assert result.best_handler.name == "greeter"

    def test_invalid_regex_raises_at_init(self):
        rules = [Rule(pattern=r"[invalid", handler="h", intent="i")]
        with pytest.raises(re.error):
            RuleRouter(rules)

    def test_non_list_rules_raises_type_error(self):
        with pytest.raises(TypeError, match="rules must be a list"):
            RuleRouter("not a list")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_empty_rules_list_returns_empty(self):
        router = RuleRouter([])
        result = await router.classify("anything", make_ctx())
        assert result.intent == "unknown"
        assert result.best_handler is None


# ===================================================================
# Edge cases: message content
# ===================================================================


class TestRuleRouterEdgeCases:
    """Boundary inputs for message classification."""

    @pytest.mark.asyncio
    async def test_empty_message_returns_empty_result(self):
        rules = [Rule(pattern=r".*", handler="catch_all", intent="any")]
        router = RuleRouter(rules)
        result = await router.classify("", make_ctx())
        assert result.intent == "unknown"

    @pytest.mark.asyncio
    async def test_whitespace_only_message_returns_empty(self):
        rules = [Rule(pattern=r".*", handler="catch_all", intent="any")]
        router = RuleRouter(rules)
        result = await router.classify("   \t\n  ", make_ctx())
        assert result.intent == "unknown"

    @pytest.mark.asyncio
    async def test_very_long_message(self):
        rules = [Rule(pattern=r"needle", handler="found", intent="found")]
        router = RuleRouter(rules)
        msg = "hay " * 100_000 + "needle"
        result = await router.classify(msg, make_ctx())
        assert result.best_handler is not None
        assert result.best_handler.name == "found"

    @pytest.mark.asyncio
    async def test_unicode_message(self):
        rules = [Rule(pattern=r"\U0001f600", handler="emoji", intent="emoji")]
        router = RuleRouter(rules)
        result = await router.classify("I feel \U0001f600 today", make_ctx())
        assert result.best_handler is not None


# ===================================================================
# IntentResult and HandlerCandidate
# ===================================================================


class TestIntentResult:
    """IntentResult and HandlerCandidate validation."""

    def test_best_handler_empty_list_returns_none(self):
        result = IntentResult(intent="x", confidence=0.5, handlers=[])
        assert result.best_handler is None

    def test_best_handler_returns_first(self):
        candidates = [
            HandlerCandidate(name="a", score=0.9),
            HandlerCandidate(name="b", score=0.5),
        ]
        result = IntentResult(intent="x", confidence=0.8, handlers=candidates)
        assert result.best_handler is not None
        assert result.best_handler.name == "a"

    def test_handler_candidate_score_below_zero_raises(self):
        with pytest.raises(ValueError, match="score must be between"):
            HandlerCandidate(name="bad", score=-0.1)

    def test_handler_candidate_score_above_one_raises(self):
        with pytest.raises(ValueError, match="score must be between"):
            HandlerCandidate(name="bad", score=1.1)

    def test_handler_candidate_boundary_scores(self):
        low = HandlerCandidate(name="low", score=0.0)
        high = HandlerCandidate(name="high", score=1.0)
        assert low.score == 0.0
        assert high.score == 1.0

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValueError, match="confidence must be between"):
            IntentResult(intent="x", confidence=-0.01, handlers=[])

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValueError, match="confidence must be between"):
            IntentResult(intent="x", confidence=1.01, handlers=[])
