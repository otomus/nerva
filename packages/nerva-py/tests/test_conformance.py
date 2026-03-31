"""Conformance tests — validate Python model serializations against generated JSON schemas.

Loads YAML schemas from spec/generated/, constructs model instances using the Python
implementation, serializes them to dicts, and validates against the corresponding
JSON schema using the jsonschema library.

Covers both positive tests (valid instances) and negative tests (invalid enum values,
missing required fields, out-of-range confidence scores).
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

from nerva.context import Event, ExecContext, Permissions, Scope, Span, TokenUsage
from nerva.memory import MemoryContext, MemoryEvent, MemoryTier
from nerva.policy import PolicyAction, PolicyDecision
from nerva.registry import (
    ComponentKind,
    HealthStatus,
    InvocationStats,
    RegistryEntry,
)
from nerva.responder import Channel, Response
from nerva.router import HandlerCandidate, IntentResult
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.tools import ToolResult, ToolSpec, ToolStatus

# ---------------------------------------------------------------------------
# Schema loading helpers
# ---------------------------------------------------------------------------

GENERATED_DIR = Path(__file__).resolve().parents[3] / "spec" / "generated"

_schema_cache: dict[str, dict] = {}


def _load_schema(name: str) -> dict:
    """Load and cache a YAML schema from the generated directory.

    Args:
        name: Schema filename (e.g. "Scope.yaml").

    Returns:
        Parsed schema dict.

    Raises:
        FileNotFoundError: If the schema file does not exist.
    """
    if name not in _schema_cache:
        path = GENERATED_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"Schema not found: {path}")
        with open(path) as fh:
            _schema_cache[name] = yaml.safe_load(fh)
    return _schema_cache[name]


def _make_ref_resolver(schema: dict) -> Draft202012Validator:
    """Build a validator that resolves $ref to sibling YAML schema files.

    The generated schemas use bare filenames (e.g. "Scope.yaml") as $ref
    targets. We build a registry mapping each $id to its loaded content.

    Args:
        schema: The root schema to validate against.

    Returns:
        A configured Draft202012Validator.
    """
    registry: dict[str, dict] = {}
    for yaml_file in GENERATED_DIR.glob("*.yaml"):
        loaded = _load_schema(yaml_file.name)
        schema_id = loaded.get("$id", yaml_file.name)
        registry[schema_id] = loaded

    from referencing import Registry as RefRegistry, Resource

    resources = [
        (sid, Resource.from_contents(s))
        for sid, s in registry.items()
    ]
    ref_registry = RefRegistry().with_resources(resources)
    return Draft202012Validator(schema, registry=ref_registry)


def _validate(schema_name: str, instance: object) -> list[str]:
    """Validate an instance against a named schema.

    Args:
        schema_name: YAML schema filename (e.g. "ExecContext.yaml").
        instance: The value to validate.

    Returns:
        List of validation error messages. Empty means valid.
    """
    schema = _load_schema(schema_name)
    validator = _make_ref_resolver(schema)
    return [e.message for e in validator.iter_errors(instance)]


def _assert_valid(schema_name: str, instance: object) -> None:
    """Assert an instance conforms to its schema.

    Args:
        schema_name: YAML schema filename.
        instance: The value to validate.
    """
    errors = _validate(schema_name, instance)
    assert not errors, f"Schema {schema_name} rejected valid instance: {errors}"


def _assert_invalid(schema_name: str, instance: object) -> None:
    """Assert an instance does NOT conform to its schema.

    Args:
        schema_name: YAML schema filename.
        instance: The value to validate.
    """
    errors = _validate(schema_name, instance)
    assert errors, f"Schema {schema_name} accepted invalid instance: {instance}"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_permissions(p: Permissions) -> dict:
    """Serialize Permissions to a schema-compatible dict.

    The schema expects arrays for roles/allowed_tools/allowed_agents,
    while Python uses frozensets.

    Args:
        p: Permissions instance.

    Returns:
        Dict matching the Permissions.yaml schema.
    """
    result: dict = {"roles": sorted(p.roles)}
    if p.allowed_tools is not None:
        result["allowed_tools"] = sorted(p.allowed_tools)
    else:
        result["allowed_tools"] = None
    if p.allowed_agents is not None:
        result["allowed_agents"] = sorted(p.allowed_agents)
    else:
        result["allowed_agents"] = None
    return result


def _serialize_token_usage(t: TokenUsage) -> dict:
    """Serialize TokenUsage to a schema-compatible dict.

    Args:
        t: TokenUsage instance.

    Returns:
        Dict matching the TokenUsage.yaml schema.
    """
    return {
        "prompt_tokens": t.prompt_tokens,
        "completion_tokens": t.completion_tokens,
        "total_tokens": t.total_tokens,
        "cost_usd": t.cost_usd,
    }


def _serialize_span(s: Span) -> dict:
    """Serialize a Span to a schema-compatible dict.

    Args:
        s: Span instance.

    Returns:
        Dict matching the Span.yaml schema.
    """
    return {
        "span_id": s.span_id,
        "name": s.name,
        "parent_id": s.parent_id,
        "started_at": s.started_at,
        "ended_at": s.ended_at,
        "attributes": dict(s.attributes),
    }


def _serialize_event(e: Event) -> dict:
    """Serialize an Event to a schema-compatible dict.

    Args:
        e: Event instance.

    Returns:
        Dict matching the Event.yaml schema.
    """
    return {
        "timestamp": e.timestamp,
        "name": e.name,
        "attributes": dict(e.attributes),
    }


def _serialize_exec_context(ctx: ExecContext) -> dict:
    """Serialize an ExecContext to a schema-compatible dict.

    The schema uses 'cancelled' as a boolean, not an asyncio.Event.

    Args:
        ctx: ExecContext instance.

    Returns:
        Dict matching the ExecContext.yaml schema.
    """
    return {
        "request_id": ctx.request_id,
        "trace_id": ctx.trace_id,
        "user_id": ctx.user_id,
        "session_id": ctx.session_id,
        "permissions": _serialize_permissions(ctx.permissions),
        "memory_scope": ctx.memory_scope.value,
        "spans": [_serialize_span(s) for s in ctx.spans],
        "events": [_serialize_event(e) for e in ctx.events],
        "token_usage": _serialize_token_usage(ctx.token_usage),
        "created_at": ctx.created_at,
        "timeout_at": ctx.timeout_at,
        "cancelled": ctx.is_cancelled(),
        "metadata": dict(ctx.metadata),
    }


def _serialize_handler_candidate(h: HandlerCandidate) -> dict:
    """Serialize a HandlerCandidate to a schema-compatible dict.

    Args:
        h: HandlerCandidate instance.

    Returns:
        Dict matching the HandlerCandidate.yaml schema.
    """
    result: dict = {"name": h.name, "score": h.score}
    if h.reason:
        result["reason"] = h.reason
    return result


def _serialize_intent_result(r: IntentResult) -> dict:
    """Serialize an IntentResult to a schema-compatible dict.

    Args:
        r: IntentResult instance.

    Returns:
        Dict matching the IntentResult.yaml schema.
    """
    result: dict = {
        "intent": r.intent,
        "confidence": r.confidence,
        "handlers": [_serialize_handler_candidate(h) for h in r.handlers],
    }
    if r.raw_scores:
        result["raw_scores"] = dict(r.raw_scores)
    return result


def _serialize_agent_input(a: AgentInput) -> dict:
    """Serialize an AgentInput to a schema-compatible dict.

    Args:
        a: AgentInput instance.

    Returns:
        Dict matching the AgentInput.yaml schema.
    """
    result: dict = {"message": a.message}
    if a.args:
        result["args"] = dict(a.args)
    if a.tools:
        result["tools"] = [dict(t) for t in a.tools]
    if a.history:
        result["history"] = [dict(h) for h in a.history]
    return result


def _serialize_agent_result(a: AgentResult) -> dict:
    """Serialize an AgentResult to a schema-compatible dict.

    Args:
        a: AgentResult instance.

    Returns:
        Dict matching the AgentResult.yaml schema.
    """
    result: dict = {"status": a.status.value}
    if a.output:
        result["output"] = a.output
    if a.data:
        result["data"] = dict(a.data)
    if a.error is not None:
        result["error"] = a.error
    else:
        result["error"] = None
    if a.handler:
        result["handler"] = a.handler
    return result


def _serialize_tool_spec(t: ToolSpec) -> dict:
    """Serialize a ToolSpec to a schema-compatible dict.

    Args:
        t: ToolSpec instance.

    Returns:
        Dict matching the ToolSpec.yaml schema.
    """
    result: dict = {"name": t.name, "description": t.description}
    if t.parameters:
        result["parameters"] = dict(t.parameters)
    if t.required_permissions:
        result["required_permissions"] = sorted(t.required_permissions)
    return result


def _serialize_tool_result(t: ToolResult) -> dict:
    """Serialize a ToolResult to a schema-compatible dict.

    Args:
        t: ToolResult instance.

    Returns:
        Dict matching the ToolResult.yaml schema.
    """
    result: dict = {"status": t.status.value}
    if t.output:
        result["output"] = t.output
    if t.error is not None:
        result["error"] = t.error
    else:
        result["error"] = None
    result["duration_ms"] = t.duration_ms
    return result


def _serialize_memory_event(m: MemoryEvent) -> dict:
    """Serialize a MemoryEvent to a schema-compatible dict.

    Args:
        m: MemoryEvent instance.

    Returns:
        Dict matching the MemoryEvent.yaml schema.
    """
    result: dict = {
        "content": m.content,
        "tier": m.tier.value,
    }
    if m.scope is not None:
        result["scope"] = m.scope.value
    else:
        result["scope"] = None
    if m.tags:
        result["tags"] = sorted(m.tags)
    if m.source:
        result["source"] = m.source
    return result


def _serialize_memory_context(m: MemoryContext) -> dict:
    """Serialize a MemoryContext to a schema-compatible dict.

    Args:
        m: MemoryContext instance.

    Returns:
        Dict matching the MemoryContext.yaml schema.
    """
    return {
        "conversation": [dict(c) for c in m.conversation],
        "episodes": list(m.episodes),
        "facts": list(m.facts),
        "knowledge": list(m.knowledge),
        "token_count": m.token_count,
    }


def _serialize_channel(c: Channel) -> dict:
    """Serialize a Channel to a schema-compatible dict.

    Args:
        c: Channel instance.

    Returns:
        Dict matching the Channel.yaml schema.
    """
    return {
        "name": c.name,
        "supports_markdown": c.supports_markdown,
        "supports_media": c.supports_media,
        "max_length": c.max_length,
    }


def _serialize_response(r: Response) -> dict:
    """Serialize a Response to a schema-compatible dict.

    Args:
        r: Response instance.

    Returns:
        Dict matching the Response.yaml schema.
    """
    return {
        "text": r.text,
        "channel": _serialize_channel(r.channel),
        "media": list(r.media),
        "metadata": dict(r.metadata),
    }


def _serialize_invocation_stats(s: InvocationStats) -> dict:
    """Serialize InvocationStats to a schema-compatible dict.

    Args:
        s: InvocationStats instance.

    Returns:
        Dict matching the InvocationStats.yaml schema.
    """
    return {
        "total_calls": s.total_calls,
        "successes": s.successes,
        "failures": s.failures,
        "last_invoked_at": s.last_invoked_at,
        "avg_duration_ms": s.avg_duration_ms,
    }


def _serialize_registry_entry(r: RegistryEntry) -> dict:
    """Serialize a RegistryEntry to a schema-compatible dict.

    Args:
        r: RegistryEntry instance.

    Returns:
        Dict matching the RegistryEntry.yaml schema.
    """
    return {
        "name": r.name,
        "kind": r.kind.value,
        "description": r.description,
        "schema": dict(r.schema) if r.schema else None,
        "metadata": dict(r.metadata),
        "health": r.health.value,
        "stats": _serialize_invocation_stats(r.stats),
        "enabled": r.enabled,
        "requirements": list(r.requirements),
        "permissions": list(r.permissions),
    }


def _serialize_policy_action(a: PolicyAction) -> dict:
    """Serialize a PolicyAction to a schema-compatible dict.

    Args:
        a: PolicyAction instance.

    Returns:
        Dict matching the PolicyAction.yaml schema.
    """
    return {
        "kind": a.kind,
        "subject": a.subject,
        "target": a.target,
        "metadata": dict(a.metadata),
    }


def _serialize_policy_decision(d: PolicyDecision) -> dict:
    """Serialize a PolicyDecision to a schema-compatible dict.

    Args:
        d: PolicyDecision instance.

    Returns:
        Dict matching the PolicyDecision.yaml schema.
    """
    result: dict = {"allowed": d.allowed}
    if d.reason is not None:
        result["reason"] = d.reason
    else:
        result["reason"] = None
    result["require_approval"] = d.require_approval
    if d.approvers is not None:
        result["approvers"] = list(d.approvers)
    else:
        result["approvers"] = None
    if d.budget_remaining is not None:
        result["budget_remaining"] = d.budget_remaining
    else:
        result["budget_remaining"] = None
    return result


# ===========================================================================
# Positive conformance tests — valid instances
# ===========================================================================


class TestExecContextConformance:
    """Validate ExecContext serialization conforms to ExecContext.yaml."""

    def test_minimal_context(self) -> None:
        """A freshly created ExecContext with defaults validates."""
        ctx = ExecContext.create()
        _assert_valid("ExecContext.yaml", _serialize_exec_context(ctx))

    def test_full_context(self) -> None:
        """An ExecContext with all fields populated validates."""
        ctx = ExecContext.create(
            user_id="u-123",
            session_id="s-456",
            permissions=Permissions(
                roles=frozenset({"admin", "user"}),
                allowed_tools=frozenset({"search"}),
                allowed_agents=frozenset({"helper"}),
            ),
            memory_scope=Scope.GLOBAL,
            timeout_seconds=30.0,
        )
        ctx.metadata["env"] = "test"
        ctx.add_span("test.span")
        ctx.add_event("test.event", detail="value")
        ctx.record_tokens(TokenUsage(10, 5, 15, 0.001))
        _assert_valid("ExecContext.yaml", _serialize_exec_context(ctx))

    def test_anonymous_context(self) -> None:
        """An ExecContext with null user_id and session_id validates."""
        ctx = ExecContext.create(user_id=None, session_id=None)
        serialized = _serialize_exec_context(ctx)
        assert serialized["user_id"] is None
        assert serialized["session_id"] is None
        _assert_valid("ExecContext.yaml", serialized)


class TestIntentResultConformance:
    """Validate IntentResult and HandlerCandidate against schemas."""

    def test_intent_with_handlers(self) -> None:
        """IntentResult with multiple handlers validates."""
        candidates = [
            HandlerCandidate(name="search", score=0.95, reason="keyword match"),
            HandlerCandidate(name="fallback", score=0.1),
        ]
        result = IntentResult(
            intent="search_web",
            confidence=0.9,
            handlers=candidates,
            raw_scores={"search": 0.95, "fallback": 0.1},
        )
        _assert_valid("IntentResult.yaml", _serialize_intent_result(result))

    def test_intent_empty_handlers(self) -> None:
        """IntentResult with no handlers validates."""
        result = IntentResult(intent="unknown", confidence=0.0, handlers=[])
        _assert_valid("IntentResult.yaml", _serialize_intent_result(result))

    def test_handler_candidate_boundary_scores(self) -> None:
        """HandlerCandidate at score boundaries (0.0 and 1.0) validates."""
        for score in (0.0, 1.0):
            h = HandlerCandidate(name="test", score=score)
            _assert_valid("HandlerCandidate.yaml", _serialize_handler_candidate(h))


class TestAgentInputConformance:
    """Validate AgentInput serialization against AgentInput.yaml."""

    def test_minimal_input(self) -> None:
        """AgentInput with only message validates."""
        inp = AgentInput(message="hello")
        _assert_valid("AgentInput.yaml", _serialize_agent_input(inp))

    def test_full_input(self) -> None:
        """AgentInput with all fields populated validates."""
        inp = AgentInput(
            message="book a flight",
            args={"destination": "NYC"},
            tools=[{"name": "calendar", "description": "manage events"}],
            history=[{"role": "user", "content": "hi"}],
        )
        _assert_valid("AgentInput.yaml", _serialize_agent_input(inp))


class TestAgentResultConformance:
    """Validate AgentResult serialization against AgentResult.yaml."""

    def test_success_result(self) -> None:
        """A successful AgentResult validates."""
        result = AgentResult(
            status=AgentStatus.SUCCESS,
            output="Done",
            handler="search",
        )
        _assert_valid("AgentResult.yaml", _serialize_agent_result(result))

    def test_error_result(self) -> None:
        """An error AgentResult with error message validates."""
        result = AgentResult(
            status=AgentStatus.ERROR,
            error="timeout exceeded",
            handler="slow_handler",
        )
        _assert_valid("AgentResult.yaml", _serialize_agent_result(result))

    def test_all_status_values(self) -> None:
        """Every AgentStatus enum value validates against AgentStatus.yaml."""
        for status in AgentStatus:
            _assert_valid("AgentStatus.yaml", status.value)


class TestToolSpecConformance:
    """Validate ToolSpec serialization against ToolSpec.yaml."""

    def test_minimal_spec(self) -> None:
        """ToolSpec with only required fields validates."""
        spec = ToolSpec(name="search", description="Search the web")
        _assert_valid("ToolSpec.yaml", _serialize_tool_spec(spec))

    def test_full_spec(self) -> None:
        """ToolSpec with parameters and permissions validates."""
        spec = ToolSpec(
            name="calendar",
            description="Manage calendar",
            parameters={"type": "object"},
            required_permissions=frozenset({"admin"}),
        )
        _assert_valid("ToolSpec.yaml", _serialize_tool_spec(spec))


class TestToolResultConformance:
    """Validate ToolResult serialization against ToolResult.yaml."""

    def test_success_result(self) -> None:
        """A successful ToolResult validates."""
        result = ToolResult(
            status=ToolStatus.SUCCESS,
            output="found 3 results",
            duration_ms=120.5,
        )
        _assert_valid("ToolResult.yaml", _serialize_tool_result(result))

    def test_error_result(self) -> None:
        """A failed ToolResult with error validates."""
        result = ToolResult(
            status=ToolStatus.PERMISSION_DENIED,
            error="not authorized",
            duration_ms=5.0,
        )
        _assert_valid("ToolResult.yaml", _serialize_tool_result(result))

    def test_all_status_values(self) -> None:
        """Every ToolStatus enum value validates."""
        for status in ToolStatus:
            _assert_valid("ToolStatus.yaml", status.value)


class TestMemoryEventConformance:
    """Validate MemoryEvent serialization against MemoryEvent.yaml."""

    def test_minimal_event(self) -> None:
        """MemoryEvent with only content and tier validates."""
        event = MemoryEvent(content="user said hello", tier=MemoryTier.HOT)
        _assert_valid("MemoryEvent.yaml", _serialize_memory_event(event))

    def test_full_event(self) -> None:
        """MemoryEvent with all fields validates."""
        event = MemoryEvent(
            content="important fact",
            tier=MemoryTier.COLD,
            scope=Scope.USER,
            tags=frozenset({"important", "fact"}),
            source="agent-x",
        )
        _assert_valid("MemoryEvent.yaml", _serialize_memory_event(event))

    def test_null_scope(self) -> None:
        """MemoryEvent with null scope (inherit from ctx) validates."""
        event = MemoryEvent(content="test", tier=MemoryTier.WARM, scope=None)
        serialized = _serialize_memory_event(event)
        assert serialized["scope"] is None
        _assert_valid("MemoryEvent.yaml", serialized)

    def test_all_tier_values(self) -> None:
        """Every MemoryTier enum value validates."""
        for tier in MemoryTier:
            _assert_valid("MemoryTier.yaml", tier.value)


class TestMemoryContextConformance:
    """Validate MemoryContext serialization against MemoryContext.yaml."""

    def test_empty_context(self) -> None:
        """An empty MemoryContext validates."""
        ctx = MemoryContext()
        _assert_valid("MemoryContext.yaml", _serialize_memory_context(ctx))

    def test_full_context(self) -> None:
        """A MemoryContext with populated fields validates."""
        ctx = MemoryContext(
            conversation=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            episodes=["episode1", "episode2"],
            facts=["fact1"],
            knowledge=["knowledge1"],
            token_count=150,
        )
        _assert_valid("MemoryContext.yaml", _serialize_memory_context(ctx))


class TestChannelConformance:
    """Validate Channel serialization against Channel.yaml."""

    def test_minimal_channel(self) -> None:
        """Channel with only name validates."""
        ch = Channel(name="api")
        _assert_valid("Channel.yaml", _serialize_channel(ch))

    def test_full_channel(self) -> None:
        """Channel with all fields validates."""
        ch = Channel(
            name="slack",
            supports_markdown=True,
            supports_media=False,
            max_length=4000,
        )
        _assert_valid("Channel.yaml", _serialize_channel(ch))


class TestResponseConformance:
    """Validate Response serialization against Response.yaml."""

    def test_minimal_response(self) -> None:
        """Response with only text and channel validates."""
        resp = Response(text="hello", channel=Channel(name="api"))
        _assert_valid("Response.yaml", _serialize_response(resp))

    def test_full_response(self) -> None:
        """Response with media and metadata validates."""
        resp = Response(
            text="Here is the image",
            channel=Channel(name="websocket", supports_markdown=True, supports_media=True),
            media=["https://example.com/image.png"],
            metadata={"format": "png"},
        )
        _assert_valid("Response.yaml", _serialize_response(resp))


class TestRegistryEntryConformance:
    """Validate RegistryEntry serialization against RegistryEntry.yaml."""

    def test_minimal_entry(self) -> None:
        """RegistryEntry with only required fields validates."""
        entry = RegistryEntry(
            name="search-agent",
            kind=ComponentKind.AGENT,
            description="Search the web",
        )
        _assert_valid("RegistryEntry.yaml", _serialize_registry_entry(entry))

    def test_full_entry(self) -> None:
        """RegistryEntry with all fields validates."""
        stats = InvocationStats(
            total_calls=100,
            successes=95,
            failures=5,
            last_invoked_at=time.time(),
            avg_duration_ms=42.0,
        )
        entry = RegistryEntry(
            name="calendar-tool",
            kind=ComponentKind.TOOL,
            description="Manage calendar events",
            schema={"type": "object"},
            metadata={"version": "1.0"},
            health=HealthStatus.HEALTHY,
            stats=stats,
            enabled=True,
            requirements=["google-creds"],
            permissions=["admin"],
        )
        _assert_valid("RegistryEntry.yaml", _serialize_registry_entry(entry))

    def test_all_component_kinds(self) -> None:
        """Every ComponentKind enum value validates."""
        for kind in ComponentKind:
            _assert_valid("ComponentKind.yaml", kind.value)

    def test_all_health_statuses(self) -> None:
        """Every HealthStatus enum value validates."""
        for status in HealthStatus:
            _assert_valid("HealthStatus.yaml", status.value)


class TestPolicyActionConformance:
    """Validate PolicyAction serialization against PolicyAction.yaml."""

    def test_minimal_action(self) -> None:
        """PolicyAction with required fields validates."""
        action = PolicyAction(
            kind="invoke_agent",
            subject="user-1",
            target="search-agent",
        )
        _assert_valid("PolicyAction.yaml", _serialize_policy_action(action))

    def test_action_with_metadata(self) -> None:
        """PolicyAction with metadata validates."""
        action = PolicyAction(
            kind="call_tool",
            subject="agent-x",
            target="web-search",
            metadata={"cost_estimate": "0.01"},
        )
        _assert_valid("PolicyAction.yaml", _serialize_policy_action(action))


class TestPolicyDecisionConformance:
    """Validate PolicyDecision serialization against PolicyDecision.yaml."""

    def test_allow_decision(self) -> None:
        """A simple allow decision validates."""
        decision = PolicyDecision(allowed=True)
        _assert_valid("PolicyDecision.yaml", _serialize_policy_decision(decision))

    def test_deny_with_reason(self) -> None:
        """A denial with reason validates."""
        decision = PolicyDecision(allowed=False, reason="rate limit exceeded")
        _assert_valid("PolicyDecision.yaml", _serialize_policy_decision(decision))

    def test_full_decision(self) -> None:
        """A decision with all fields validates."""
        decision = PolicyDecision(
            allowed=False,
            reason="budget exceeded",
            require_approval=True,
            approvers=["admin@co.com"],
            budget_remaining=42.5,
        )
        _assert_valid("PolicyDecision.yaml", _serialize_policy_decision(decision))


# ===========================================================================
# Negative conformance tests — invalid instances
# ===========================================================================


class TestNegativeEnumValues:
    """Invalid enum values must be rejected by their schemas."""

    def test_invalid_scope(self) -> None:
        """Unknown scope string is rejected."""
        _assert_invalid("Scope.yaml", "unknown")

    def test_empty_scope(self) -> None:
        """Empty string is not a valid scope."""
        _assert_invalid("Scope.yaml", "")

    def test_numeric_scope(self) -> None:
        """Numeric value is not a valid scope."""
        _assert_invalid("Scope.yaml", 42)

    def test_null_scope(self) -> None:
        """Null is not a valid scope."""
        _assert_invalid("Scope.yaml", None)

    def test_invalid_agent_status(self) -> None:
        """Unknown agent status is rejected."""
        _assert_invalid("AgentStatus.yaml", "failed")

    def test_invalid_tool_status(self) -> None:
        """Unknown tool status is rejected."""
        _assert_invalid("ToolStatus.yaml", "denied")

    def test_invalid_memory_tier(self) -> None:
        """Unknown memory tier is rejected."""
        _assert_invalid("MemoryTier.yaml", "archive")

    def test_invalid_component_kind(self) -> None:
        """Unknown component kind is rejected."""
        _assert_invalid("ComponentKind.yaml", "service")

    def test_invalid_health_status(self) -> None:
        """Unknown health status is rejected."""
        _assert_invalid("HealthStatus.yaml", "down")


class TestNegativeMissingRequiredFields:
    """Missing required fields must cause schema rejection."""

    def test_exec_context_missing_request_id(self) -> None:
        """ExecContext without request_id is rejected."""
        ctx = ExecContext.create()
        serialized = _serialize_exec_context(ctx)
        del serialized["request_id"]
        _assert_invalid("ExecContext.yaml", serialized)

    def test_handler_candidate_missing_name(self) -> None:
        """HandlerCandidate without name is rejected."""
        _assert_invalid("HandlerCandidate.yaml", {"score": 0.5})

    def test_handler_candidate_missing_score(self) -> None:
        """HandlerCandidate without score is rejected."""
        _assert_invalid("HandlerCandidate.yaml", {"name": "test"})

    def test_intent_result_missing_intent(self) -> None:
        """IntentResult without intent is rejected."""
        _assert_invalid("IntentResult.yaml", {"confidence": 0.5, "handlers": []})

    def test_intent_result_missing_confidence(self) -> None:
        """IntentResult without confidence is rejected."""
        _assert_invalid("IntentResult.yaml", {"intent": "test", "handlers": []})

    def test_intent_result_missing_handlers(self) -> None:
        """IntentResult without handlers is rejected."""
        _assert_invalid("IntentResult.yaml", {"intent": "test", "confidence": 0.5})

    def test_agent_input_missing_message(self) -> None:
        """AgentInput without message is rejected."""
        _assert_invalid("AgentInput.yaml", {"args": {}})

    def test_agent_result_missing_status(self) -> None:
        """AgentResult without status is rejected."""
        _assert_invalid("AgentResult.yaml", {"output": "hello"})

    def test_tool_spec_missing_name(self) -> None:
        """ToolSpec without name is rejected."""
        _assert_invalid("ToolSpec.yaml", {"description": "test"})

    def test_tool_spec_missing_description(self) -> None:
        """ToolSpec without description is rejected."""
        _assert_invalid("ToolSpec.yaml", {"name": "test"})

    def test_tool_result_missing_status(self) -> None:
        """ToolResult without status is rejected."""
        _assert_invalid("ToolResult.yaml", {"output": "hello"})

    def test_memory_event_missing_content(self) -> None:
        """MemoryEvent without content is rejected."""
        _assert_invalid("MemoryEvent.yaml", {"tier": "hot"})

    def test_memory_event_missing_tier(self) -> None:
        """MemoryEvent without tier is rejected."""
        _assert_invalid("MemoryEvent.yaml", {"content": "test"})

    def test_channel_missing_name(self) -> None:
        """Channel without name is rejected."""
        _assert_invalid("Channel.yaml", {"supports_markdown": True})

    def test_response_missing_text(self) -> None:
        """Response without text is rejected."""
        _assert_invalid("Response.yaml", {"channel": {"name": "api"}})

    def test_response_missing_channel(self) -> None:
        """Response without channel is rejected."""
        _assert_invalid("Response.yaml", {"text": "hello"})

    def test_registry_entry_missing_name(self) -> None:
        """RegistryEntry without name is rejected."""
        _assert_invalid("RegistryEntry.yaml", {"kind": "agent", "description": "test"})

    def test_registry_entry_missing_kind(self) -> None:
        """RegistryEntry without kind is rejected."""
        _assert_invalid("RegistryEntry.yaml", {"name": "test", "description": "test"})

    def test_registry_entry_missing_description(self) -> None:
        """RegistryEntry without description is rejected."""
        _assert_invalid("RegistryEntry.yaml", {"name": "test", "kind": "agent"})

    def test_policy_action_missing_kind(self) -> None:
        """PolicyAction without kind is rejected."""
        _assert_invalid("PolicyAction.yaml", {"subject": "u1", "target": "t1"})

    def test_policy_action_missing_subject(self) -> None:
        """PolicyAction without subject is rejected."""
        _assert_invalid("PolicyAction.yaml", {"kind": "invoke_agent", "target": "t1"})

    def test_policy_action_missing_target(self) -> None:
        """PolicyAction without target is rejected."""
        _assert_invalid("PolicyAction.yaml", {"kind": "invoke_agent", "subject": "u1"})

    def test_policy_decision_missing_allowed(self) -> None:
        """PolicyDecision without allowed is rejected."""
        _assert_invalid("PolicyDecision.yaml", {"reason": "test"})

    def test_permissions_missing_roles(self) -> None:
        """Permissions without roles is rejected."""
        _assert_invalid("Permissions.yaml", {})

    def test_token_usage_missing_fields(self) -> None:
        """TokenUsage missing required fields is rejected."""
        _assert_invalid("TokenUsage.yaml", {"prompt_tokens": 0})

    def test_empty_object(self) -> None:
        """An empty object is rejected for all object schemas with required fields."""
        schemas_with_required = [
            "ExecContext.yaml",
            "HandlerCandidate.yaml",
            "IntentResult.yaml",
            "AgentInput.yaml",
            "AgentResult.yaml",
            "ToolSpec.yaml",
            "ToolResult.yaml",
            "MemoryEvent.yaml",
            "Channel.yaml",
            "Response.yaml",
            "RegistryEntry.yaml",
            "PolicyAction.yaml",
            "PolicyDecision.yaml",
            "Permissions.yaml",
            "TokenUsage.yaml",
        ]
        for schema_name in schemas_with_required:
            _assert_invalid(schema_name, {})


class TestNegativeOutOfRangeScores:
    """Out-of-range confidence and score values must be rejected."""

    def test_handler_score_above_1(self) -> None:
        """HandlerCandidate with score > 1.0 is rejected."""
        _assert_invalid("HandlerCandidate.yaml", {"name": "test", "score": 1.5})

    def test_handler_score_below_0(self) -> None:
        """HandlerCandidate with score < 0.0 is rejected."""
        _assert_invalid("HandlerCandidate.yaml", {"name": "test", "score": -0.1})

    def test_intent_confidence_above_1(self) -> None:
        """IntentResult with confidence > 1.0 is rejected."""
        _assert_invalid(
            "IntentResult.yaml",
            {"intent": "test", "confidence": 1.5, "handlers": []},
        )

    def test_intent_confidence_below_0(self) -> None:
        """IntentResult with confidence < 0.0 is rejected."""
        _assert_invalid(
            "IntentResult.yaml",
            {"intent": "test", "confidence": -0.1, "handlers": []},
        )


class TestNegativeWrongTypes:
    """Wrong types for fields must be rejected."""

    def test_permissions_roles_as_string(self) -> None:
        """Permissions with roles as a string (not array) is rejected."""
        _assert_invalid("Permissions.yaml", {"roles": "admin"})

    def test_permissions_roles_with_non_string_items(self) -> None:
        """Permissions with non-string items in roles is rejected."""
        _assert_invalid("Permissions.yaml", {"roles": [42]})

    def test_token_usage_string_tokens(self) -> None:
        """TokenUsage with string prompt_tokens is rejected."""
        _assert_invalid("TokenUsage.yaml", {
            "prompt_tokens": "ten",
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        })

    def test_policy_decision_allowed_as_string(self) -> None:
        """PolicyDecision with allowed as string is rejected."""
        _assert_invalid("PolicyDecision.yaml", {"allowed": "yes"})

    def test_handler_score_as_string(self) -> None:
        """HandlerCandidate with string score is rejected."""
        _assert_invalid("HandlerCandidate.yaml", {"name": "test", "score": "high"})

    def test_channel_name_as_number(self) -> None:
        """Channel with numeric name is rejected."""
        _assert_invalid("Channel.yaml", {"name": 42})

    def test_agent_input_message_as_number(self) -> None:
        """AgentInput with numeric message is rejected."""
        _assert_invalid("AgentInput.yaml", {"message": 42})
