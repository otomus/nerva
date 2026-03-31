---
title: Policy
description: Declarative safety, rate limits, budgets, and approval gates.
---

The PolicyEngine evaluates and records governance decisions at every execution stage. It controls who can do what, how much, and whether a human must approve.

## Protocol

```python
class PolicyEngine(Protocol):
    async def evaluate(self, action: PolicyAction, ctx: ExecContext) -> PolicyDecision:
        ...

    async def record(self, action: PolicyAction, decision: PolicyDecision, ctx: ExecContext) -> None:
        ...
```

### Value types

```python
@dataclass(frozen=True)
class PolicyAction:
    kind: str       # "invoke_agent", "call_tool", "delegate", "store_memory", "route"
    subject: str    # who is acting (user_id or agent_name)
    target: str     # what they are acting on (agent_name, tool_name)
    metadata: dict[str, str]

@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None
    require_approval: bool = False
    approvers: list[str] | None = None
    budget_remaining: float | None = None
```

## Strategies

### NoopPolicyEngine

Allows everything. Use for development and testing.

```python
from nerva.policy.noop import NoopPolicyEngine

policy = NoopPolicyEngine()
decision = await policy.evaluate(action, ctx)
# decision.allowed == True (always)
```

### YamlPolicyEngine

Loads rules from a YAML config file and evaluates four dimensions:

```python
from nerva.policy.yaml_engine import YamlPolicyEngine

policy = YamlPolicyEngine(config_path="nerva.yaml")
```

**Configuration:**

```yaml
policies:
  rate_limit:
    per_user:
      max_requests_per_minute: 30
      on_exceed: reject        # reject | queue | warn

  budget:
    per_agent:
      max_tokens_per_hour: 100000
      max_cost_per_day_usd: 5.00
      on_exceed: pause         # block | pause | warn | degrade

  approval:
    agents:
      - name: deploy_agent
        requires_approval: true
        approvers: [admin, devops]

  execution:
    max_depth: 5               # delegation depth limit
    max_tool_calls_per_invocation: 20
    timeout_seconds: 30
```

**Evaluation order:** rate limit -> budget -> approval -> execution limits. First denial short-circuits.

```python
action = PolicyAction(kind="invoke_agent", subject="user_1", target="deploy_agent")
decision = await policy.evaluate(action, ctx)

if decision.require_approval:
    print(f"Needs approval from: {decision.approvers}")
elif not decision.allowed:
    print(f"Denied: {decision.reason}")
```

### AdaptivePolicyEngine

Extends `YamlPolicyEngine` with runtime condition monitoring. Adapts limits based on system load, error rates, or custom signals.

```python
from nerva.policy.adaptive import AdaptivePolicyEngine

policy = AdaptivePolicyEngine(
    config_path="nerva.yaml",
    conditions={
        "high_load": lambda ctx: ctx.metadata.get("system_load", "0") > "0.8",
    },
    adaptations={
        "high_load": {"rate_limit_max_per_minute": 10},  # tighten under load
    },
)
```

## Per-agent decorator overrides

Override YAML defaults on a per-agent basis using the `@agent` decorator:

```python
from nerva.policy.decorator import agent

@agent(name="deploy_agent", policy={
    "requires_approval": True,
    "timeout_seconds": 120,
    "max_tool_calls": 5,
    "max_cost_usd": 1.00,
    "approvers": ["admin"],
})
class DeployAgent:
    async def handle(self, input, ctx):
        ...
```

Resolution order: **YAML defaults -> decorator overrides**. Decorator values win for any field they set.

## Policy dimensions

### Rate limiting

Sliding-window per-user request rate. Tracks timestamps in memory with automatic window cleanup.

```python
# Evaluate
decision = await policy.evaluate(
    PolicyAction(kind="invoke_agent", subject="user_1", target="any"),
    ctx,
)
# "rate limit exceeded: 31/30 requests per minute (on_exceed=reject)"

# Record (only updates counters when allowed)
await policy.record(action, decision, ctx)
```

### Budget enforcement

Per-agent token and cost ceilings with configurable on-exceed behavior:

| on_exceed | Behavior |
|-----------|----------|
| `block` | Deny the action immediately |
| `pause` | Deny and signal the orchestrator to queue |
| `warn` | Allow but log a warning |
| `degrade` | Allow with reduced capability |

Budget tracking reads `ctx.token_usage.total_tokens` and `ctx.token_usage.cost_usd`.

### Approval gates

Named agents that require human sign-off. The decision returns `require_approval=True` with the approver list. Your application is responsible for the approval UI -- Nerva only gates execution.

### Execution limits

Guards against runaway recursion and unbounded tool usage:

- **max_depth** -- maximum delegation depth for a single request chain
- **max_tool_calls_per_invocation** -- tool call ceiling per agent invocation
- **timeout_seconds** -- per-action timeout
