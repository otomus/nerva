"""YAML-based policy engine — load rules from config, evaluate at runtime.

Supports four policy dimensions:

* **Rate limiting** — per-user requests-per-minute with sliding window.
* **Budget** — per-agent token and cost caps with configurable on-exceed action.
* **Approval** — named agents that require human sign-off before execution.
* **Execution** — depth and tool-call guards to prevent runaway recursion.

Example YAML structure::

    policies:
      budget:
        per_agent:
          max_tokens_per_hour: 100000
          max_cost_per_day_usd: 5.00
          on_exceed: pause
      rate_limit:
        per_user:
          max_requests_per_minute: 30
          on_exceed: queue
      approval:
        agents:
          - name: deploy_agent
            requires_approval: true
            approvers: [admin]
      execution:
        max_depth: 5
        max_tool_calls_per_invocation: 20
        timeout_seconds: 30
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

import yaml

from nerva.policy import ALLOW, PolicyAction, PolicyDecision

if TYPE_CHECKING:
    from nerva.context import ExecContext


# ---------------------------------------------------------------------------
# Named constants
# ---------------------------------------------------------------------------

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3_600
SECONDS_PER_DAY = 86_400

DEFAULT_MAX_DEPTH = 10
DEFAULT_MAX_TOOL_CALLS = 50
DEFAULT_TIMEOUT_SECONDS = 30.0

ON_EXCEED_BLOCK = "block"
ON_EXCEED_REJECT = "reject"
ON_EXCEED_PAUSE = "pause"
ON_EXCEED_WARN = "warn"
ON_EXCEED_QUEUE = "queue"
ON_EXCEED_DEGRADE = "degrade"

UNLIMITED = 0


# ---------------------------------------------------------------------------
# Parsed config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyConfig:
    """Parsed, validated policy configuration.

    Zero values for limits mean "unlimited" (no enforcement).

    Attributes:
        budget_max_tokens_per_hour: Token ceiling per agent per hour.
        budget_max_cost_per_day_usd: Dollar ceiling per agent per day.
        budget_on_exceed: Strategy when budget is exceeded.
        rate_limit_max_per_minute: Request ceiling per user per minute.
        rate_limit_on_exceed: Strategy when rate limit is hit.
        approval_agents: Mapping of agent name to list of approver roles.
        max_depth: Maximum delegation depth for a single request.
        max_tool_calls: Maximum tool invocations per single agent invocation.
        timeout_seconds: Per-action timeout in seconds.
    """

    budget_max_tokens_per_hour: int = UNLIMITED
    budget_max_cost_per_day_usd: float = 0.0
    budget_on_exceed: str = ON_EXCEED_BLOCK
    rate_limit_max_per_minute: int = UNLIMITED
    rate_limit_on_exceed: str = ON_EXCEED_REJECT
    approval_agents: dict[str, list[str]] = field(default_factory=dict)
    max_depth: int = DEFAULT_MAX_DEPTH
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def parse_policy_config(raw: dict[str, object]) -> PolicyConfig:
    """Parse a raw YAML dict into a validated ``PolicyConfig``.

    Looks for a top-level ``policies`` key. Missing sections use defaults.

    Args:
        raw: Parsed YAML dictionary (may or may not contain ``policies``).

    Returns:
        A fully populated ``PolicyConfig``.
    """
    policies = raw.get("policies", {})
    if not isinstance(policies, dict):
        return PolicyConfig()

    budget = _extract_budget(policies)
    rate_limit = _extract_rate_limit(policies)
    approval_agents = _extract_approval_agents(policies)
    execution = _extract_execution(policies)

    return PolicyConfig(
        budget_max_tokens_per_hour=budget[0],
        budget_max_cost_per_day_usd=budget[1],
        budget_on_exceed=budget[2],
        rate_limit_max_per_minute=rate_limit[0],
        rate_limit_on_exceed=rate_limit[1],
        approval_agents=approval_agents,
        max_depth=execution[0],
        max_tool_calls=execution[1],
        timeout_seconds=execution[2],
    )


def _extract_budget(
    policies: dict[str, object],
) -> tuple[int, float, str]:
    """Extract budget fields from the policies dict.

    Args:
        policies: The ``policies`` section of the YAML config.

    Returns:
        Tuple of (max_tokens_per_hour, max_cost_per_day_usd, on_exceed).
    """
    budget = policies.get("budget", {})
    if not isinstance(budget, dict):
        return (UNLIMITED, 0.0, ON_EXCEED_BLOCK)

    per_agent = budget.get("per_agent", {})
    if not isinstance(per_agent, dict):
        return (UNLIMITED, 0.0, ON_EXCEED_BLOCK)

    return (
        int(per_agent.get("max_tokens_per_hour", UNLIMITED)),
        float(per_agent.get("max_cost_per_day_usd", 0.0)),
        str(per_agent.get("on_exceed", ON_EXCEED_BLOCK)),
    )


def _extract_rate_limit(
    policies: dict[str, object],
) -> tuple[int, str]:
    """Extract rate limit fields from the policies dict.

    Args:
        policies: The ``policies`` section of the YAML config.

    Returns:
        Tuple of (max_requests_per_minute, on_exceed).
    """
    rate_limit = policies.get("rate_limit", {})
    if not isinstance(rate_limit, dict):
        return (UNLIMITED, ON_EXCEED_REJECT)

    per_user = rate_limit.get("per_user", {})
    if not isinstance(per_user, dict):
        return (UNLIMITED, ON_EXCEED_REJECT)

    return (
        int(per_user.get("max_requests_per_minute", UNLIMITED)),
        str(per_user.get("on_exceed", ON_EXCEED_REJECT)),
    )


def _extract_approval_agents(
    policies: dict[str, object],
) -> dict[str, list[str]]:
    """Extract approval agent mappings from the policies dict.

    Args:
        policies: The ``policies`` section of the YAML config.

    Returns:
        Dict mapping agent names to their required approver lists.
    """
    approval = policies.get("approval", {})
    if not isinstance(approval, dict):
        return {}

    agents_list = approval.get("agents", [])
    if not isinstance(agents_list, list):
        return {}

    result: dict[str, list[str]] = {}
    for entry in agents_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        if not entry.get("requires_approval", False):
            continue
        approvers = entry.get("approvers", [])
        if isinstance(approvers, list):
            result[name] = [str(a) for a in approvers]

    return result


def _extract_execution(
    policies: dict[str, object],
) -> tuple[int, int, float]:
    """Extract execution limit fields from the policies dict.

    Args:
        policies: The ``policies`` section of the YAML config.

    Returns:
        Tuple of (max_depth, max_tool_calls, timeout_seconds).
    """
    execution = policies.get("execution", {})
    if not isinstance(execution, dict):
        return (DEFAULT_MAX_DEPTH, DEFAULT_MAX_TOOL_CALLS, DEFAULT_TIMEOUT_SECONDS)

    return (
        int(execution.get("max_depth", DEFAULT_MAX_DEPTH)),
        int(execution.get("max_tool_calls_per_invocation", DEFAULT_MAX_TOOL_CALLS)),
        float(execution.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class YamlPolicyEngine:
    """Policy engine that loads rules from YAML configuration.

    Evaluates budget, rate limit, approval, and execution policies.
    Tracks per-user request timestamps and per-agent token usage in memory.
    All mutable state is protected by a lock for thread safety.

    Args:
        config_path: Path to a YAML file containing a ``policies`` section.
        config_dict: Pre-parsed dict (used instead of ``config_path`` if given).

    Raises:
        ValueError: If neither ``config_path`` nor ``config_dict`` is provided.
        FileNotFoundError: If ``config_path`` does not exist.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        config_dict: dict[str, object] | None = None,
    ) -> None:
        raw = _load_raw_config(config_path, config_dict)
        self._config: PolicyConfig = parse_policy_config(raw)
        self._lock = Lock()

        # Sliding window: user_id -> list of request timestamps
        self._request_timestamps: dict[str, list[float]] = {}

        # Token tracking: agent_name -> list of (timestamp, token_count)
        self._token_ledger: dict[str, list[tuple[float, int]]] = {}

        # Cost tracking: agent_name -> list of (timestamp, cost_usd)
        self._cost_ledger: dict[str, list[tuple[float, float]]] = {}

    @property
    def config(self) -> PolicyConfig:
        """The parsed policy configuration.

        Returns:
            The immutable ``PolicyConfig`` loaded at init time.
        """
        return self._config

    # -- Public protocol ----------------------------------------------------

    async def evaluate(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Evaluate action against all applicable policies.

        Checks run in order: rate limit, budget, approval, execution limits.
        The first denial short-circuits — remaining checks are skipped.

        Args:
            action: The action to evaluate.
            ctx: Execution context carrying identity, usage, and metadata.

        Returns:
            A ``PolicyDecision`` — either ``ALLOW`` or a denial/approval-required.
        """
        for check in (
            self._check_rate_limit,
            self._check_budget,
            self._check_approval,
            self._check_execution,
        ):
            decision = check(action, ctx)
            if not decision.allowed or decision.require_approval:
                return decision

        return ALLOW

    async def record(
        self,
        action: PolicyAction,
        decision: PolicyDecision,
        ctx: ExecContext,
    ) -> None:
        """Record the decision and update internal counters.

        Only updates counters when the action was allowed — denied actions
        should not consume budget or rate-limit quota.

        Args:
            action: The evaluated action.
            decision: The decision that was made.
            ctx: Execution context at the time of decision.
        """
        if not decision.allowed:
            return

        now = time.time()
        user_id = ctx.user_id or "anonymous"

        with self._lock:
            self._request_timestamps.setdefault(user_id, []).append(now)
            self._record_token_usage(action.target, now, ctx)

    # -- Individual checks --------------------------------------------------

    def _check_rate_limit(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Check per-user request rate against the configured limit.

        Args:
            action: The action being evaluated.
            ctx: Execution context carrying user identity.

        Returns:
            ``ALLOW`` or a denial with reason and on-exceed strategy.
        """
        limit = self._config.rate_limit_max_per_minute
        if limit == UNLIMITED:
            return ALLOW

        user_id = ctx.user_id or "anonymous"
        now = time.time()
        cutoff = now - SECONDS_PER_MINUTE

        with self._lock:
            timestamps = self._request_timestamps.get(user_id, [])
            recent = [ts for ts in timestamps if ts > cutoff]
            self._request_timestamps[user_id] = recent

        if len(recent) >= limit:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"rate limit exceeded: {len(recent)}/{limit} "
                    f"requests per minute (on_exceed={self._config.rate_limit_on_exceed})"
                ),
            )

        return ALLOW

    def _check_budget(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Check per-agent token and cost budgets.

        Args:
            action: The action being evaluated (target = agent name).
            ctx: Execution context carrying accumulated token usage.

        Returns:
            ``ALLOW`` or a denial with remaining budget info.
        """
        token_decision = self._check_token_budget(action.target)
        if not token_decision.allowed:
            return token_decision

        return self._check_cost_budget(action.target)

    def _check_token_budget(self, agent_name: str) -> PolicyDecision:
        """Check hourly token consumption for an agent.

        Args:
            agent_name: The agent whose budget to check.

        Returns:
            ``ALLOW`` or a denial if the token ceiling is breached.
        """
        limit = self._config.budget_max_tokens_per_hour
        if limit == UNLIMITED:
            return ALLOW

        now = time.time()
        cutoff = now - SECONDS_PER_HOUR

        with self._lock:
            entries = self._token_ledger.get(agent_name, [])
            recent = [(ts, tokens) for ts, tokens in entries if ts > cutoff]
            self._token_ledger[agent_name] = recent

        total_tokens = sum(tokens for _, tokens in recent)
        remaining = limit - total_tokens

        if remaining <= 0:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"token budget exceeded: {total_tokens}/{limit} "
                    f"tokens per hour (on_exceed={self._config.budget_on_exceed})"
                ),
                budget_remaining=0.0,
            )

        return ALLOW

    def _check_cost_budget(self, agent_name: str) -> PolicyDecision:
        """Check daily cost consumption for an agent.

        Args:
            agent_name: The agent whose cost budget to check.

        Returns:
            ``ALLOW`` or a denial if the daily cost ceiling is breached.
        """
        limit = self._config.budget_max_cost_per_day_usd
        if limit <= 0.0:
            return ALLOW

        now = time.time()
        cutoff = now - SECONDS_PER_DAY

        with self._lock:
            entries = self._cost_ledger.get(agent_name, [])
            recent = [(ts, cost) for ts, cost in entries if ts > cutoff]
            self._cost_ledger[agent_name] = recent

        total_cost = sum(cost for _, cost in recent)
        remaining = limit - total_cost

        if remaining <= 0:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"cost budget exceeded: ${total_cost:.2f}/${limit:.2f} "
                    f"per day (on_exceed={self._config.budget_on_exceed})"
                ),
                budget_remaining=0.0,
            )

        return PolicyDecision(allowed=True, budget_remaining=remaining)

    def _check_approval(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Check whether the target agent requires human approval.

        Args:
            action: The action being evaluated (target = agent name).
            ctx: Execution context (unused but kept for consistent signature).

        Returns:
            ``ALLOW`` or a require-approval decision with the approver list.
        """
        approvers = self._config.approval_agents.get(action.target)
        if approvers is None:
            return ALLOW

        return PolicyDecision(
            allowed=True,
            require_approval=True,
            approvers=list(approvers),
            reason=f"agent '{action.target}' requires approval",
        )

    def _check_execution(
        self, action: PolicyAction, ctx: ExecContext
    ) -> PolicyDecision:
        """Check execution depth and tool-call count limits.

        Reads ``depth`` and ``tool_call_count`` from ``ctx.metadata``.

        Args:
            action: The action being evaluated.
            ctx: Execution context carrying metadata with depth/tool counts.

        Returns:
            ``ALLOW`` or a denial if execution limits are breached.
        """
        depth_decision = self._check_depth(ctx)
        if not depth_decision.allowed:
            return depth_decision

        return self._check_tool_call_count(ctx)

    def _check_depth(self, ctx: ExecContext) -> PolicyDecision:
        """Check current delegation depth against the configured maximum.

        Args:
            ctx: Execution context with ``depth`` in metadata.

        Returns:
            ``ALLOW`` or a denial if depth exceeds the limit.
        """
        depth_str = ctx.metadata.get("depth", "0")
        depth = int(depth_str) if depth_str.isdigit() else 0

        if depth > self._config.max_depth:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"execution depth {depth} exceeds maximum "
                    f"{self._config.max_depth}"
                ),
            )

        return ALLOW

    def _check_tool_call_count(self, ctx: ExecContext) -> PolicyDecision:
        """Check accumulated tool-call count against the configured maximum.

        Args:
            ctx: Execution context with ``tool_call_count`` in metadata.

        Returns:
            ``ALLOW`` or a denial if tool calls exceed the limit.
        """
        count_str = ctx.metadata.get("tool_call_count", "0")
        count = int(count_str) if count_str.isdigit() else 0

        if count > self._config.max_tool_calls:
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"tool call count {count} exceeds maximum "
                    f"{self._config.max_tool_calls}"
                ),
            )

        return ALLOW

    # -- Internal helpers ---------------------------------------------------

    def _record_token_usage(
        self, agent_name: str, now: float, ctx: ExecContext
    ) -> None:
        """Append current token usage and cost to the tracking ledgers.

        Must be called while holding ``self._lock``.

        Args:
            agent_name: Agent whose budget to charge.
            now: Current timestamp.
            ctx: Execution context with accumulated token usage.
        """
        tokens = ctx.token_usage.total_tokens
        if tokens > 0:
            self._token_ledger.setdefault(agent_name, []).append(
                (now, tokens)
            )

        cost = ctx.token_usage.cost_usd
        if cost > 0.0:
            self._cost_ledger.setdefault(agent_name, []).append(
                (now, cost)
            )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_raw_config(
    config_path: str | Path | None,
    config_dict: dict[str, object] | None,
) -> dict[str, object]:
    """Load raw config from a file path or pre-parsed dict.

    Args:
        config_path: Path to a YAML file, or ``None``.
        config_dict: Pre-parsed dict, or ``None``.

    Returns:
        The raw configuration dictionary.

    Raises:
        ValueError: If neither argument is provided.
        FileNotFoundError: If ``config_path`` does not exist.
    """
    if config_dict is not None:
        return config_dict

    if config_path is None:
        raise ValueError(
            "Either config_path or config_dict must be provided"
        )

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Policy config not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)

    if not isinstance(loaded, dict):
        return {}

    return loaded
