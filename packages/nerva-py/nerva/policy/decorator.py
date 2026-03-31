"""Policy decorator — per-agent policy overrides merged with YAML defaults.

Allows agents to declare policy overrides via a ``@agent`` decorator. At
evaluation time, decorator overrides are merged on top of YAML-loaded defaults
so that code-level declarations win over config-file defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, TypeVar

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# Override config
# ---------------------------------------------------------------------------

_OVERRIDE_FIELDS = (
    "requires_approval",
    "timeout_seconds",
    "max_tool_calls",
    "max_cost_usd",
    "approvers",
)
"""Fields on ``AgentPolicyConfig`` that can override YAML values."""


@dataclass(frozen=True)
class AgentPolicyConfig:
    """Per-agent policy overrides from the ``@agent`` decorator.

    Every field defaults to ``None``, meaning "no override — use YAML default".
    Only non-``None`` values participate in the merge.

    Attributes:
        requires_approval: Override approval requirement.
        timeout_seconds: Override execution timeout.
        max_tool_calls: Override max tool calls per invocation.
        max_cost_usd: Override per-invocation cost limit.
        approvers: Override approver list.
    """

    requires_approval: bool | None = None
    timeout_seconds: float | None = None
    max_tool_calls: int | None = None
    max_cost_usd: float | None = None
    approvers: list[str] | None = None


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_agent_policies: dict[str, AgentPolicyConfig] = {}


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def agent(name: str, *, policy: dict[str, object] | None = None) -> Callable[[_T], _T]:
    """Decorator to register per-agent policy overrides.

    Usage::

        @agent(name="deploy_agent", policy={"requires_approval": True, "timeout_seconds": 120})
        class DeployAgent:
            ...

    The decorated class or function is returned unchanged. Its policy config
    is stored in a module-level registry keyed by *name*.

    Args:
        name: Agent name matching the registry entry.
        policy: Policy overrides as a dict. Keys must match
            ``AgentPolicyConfig`` field names; unknown keys are ignored.

    Returns:
        Decorator that registers the class/function and returns it unchanged.

    Raises:
        ValueError: If *name* is empty.
    """
    if not name:
        raise ValueError("agent name must be a non-empty string")

    config = _build_config(policy or {})

    def decorator(cls_or_fn: _T) -> _T:
        _agent_policies[name] = config
        return cls_or_fn

    return decorator


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def get_agent_policy(name: str) -> AgentPolicyConfig | None:
    """Look up decorator policy for a named agent.

    Args:
        name: Agent name to look up.

    Returns:
        The ``AgentPolicyConfig`` if one was registered, otherwise ``None``.
    """
    return _agent_policies.get(name)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def resolve_policy(yaml_config: dict[str, object], agent_name: str) -> dict[str, object]:
    """Merge YAML defaults with decorator overrides. Decorator wins.

    Resolution order: YAML defaults -> decorator overrides.
    Only non-``None`` decorator fields replace YAML values.

    Args:
        yaml_config: Base policy config from YAML.
        agent_name: Agent to look up decorator overrides for.

    Returns:
        Merged policy config dict with decorator values taking precedence.
    """
    override = _agent_policies.get(agent_name)
    if override is None:
        return dict(yaml_config)

    return _merge_override(yaml_config, override)


# ---------------------------------------------------------------------------
# Testing helpers
# ---------------------------------------------------------------------------


def clear_registry() -> None:
    """Remove all registered agent policies.

    Intended for test teardown — not for production use.
    """
    _agent_policies.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_config(raw: dict[str, object]) -> AgentPolicyConfig:
    """Build an ``AgentPolicyConfig`` from a raw dict, ignoring unknown keys.

    Args:
        raw: Dictionary of override values.

    Returns:
        An ``AgentPolicyConfig`` populated from *raw*.
    """
    kwargs: dict[str, object] = {}
    for key in _OVERRIDE_FIELDS:
        if key in raw:
            kwargs[key] = raw[key]
    return AgentPolicyConfig(**kwargs)  # type: ignore[arg-type]


def _merge_override(
    base: dict[str, object],
    override: AgentPolicyConfig,
) -> dict[str, object]:
    """Apply non-None override fields on top of *base*.

    Args:
        base: YAML-sourced config dict.
        override: Decorator-sourced overrides.

    Returns:
        New dict with override values replacing base values where set.
    """
    merged = dict(base)
    for key in _OVERRIDE_FIELDS:
        value = getattr(override, key)
        if value is not None:
            merged[key] = value
    return merged
