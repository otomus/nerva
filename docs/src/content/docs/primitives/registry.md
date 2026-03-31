---
title: Registry
description: Unified catalog of agents, tools, and plugins.
---

The Registry is the single source of truth for everything in the system. Every other primitive queries it for discovery instead of maintaining its own catalog.

## Protocol

```python
class Registry(Protocol):
    async def register(self, entry: RegistryEntry, ctx: ExecContext) -> None: ...
    async def discover(self, kind: ComponentKind, ctx: ExecContext) -> list[RegistryEntry]: ...
    async def resolve(self, name: str, ctx: ExecContext) -> RegistryEntry | None: ...
    async def health(self, name: str) -> HealthStatus: ...
    async def update(self, name: str, patch: RegistryPatch) -> None: ...
```

### Value types

```python
class ComponentKind(StrEnum):
    AGENT = "agent"
    TOOL = "tool"
    SENSE = "sense"
    PLUGIN = "plugin"

class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

@dataclass
class RegistryEntry:
    name: str
    kind: ComponentKind
    description: str
    schema: dict[str, object] | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    health: HealthStatus = HealthStatus.HEALTHY
    stats: InvocationStats = field(default_factory=InvocationStats)
    enabled: bool = True
    requirements: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)

@dataclass
class InvocationStats:
    total_calls: int = 0
    successes: int = 0
    failures: int = 0
    last_invoked_at: float | None = None
    avg_duration_ms: float = 0.0   # exponential moving average
```

## Strategies

### InMemoryRegistry

Dictionary-backed. No persistence. Suitable for testing and single-process deployments.

```python
from nerva.registry.inmemory import InMemoryRegistry

registry = InMemoryRegistry()

await registry.register(RegistryEntry(
    name="weather_agent",
    kind=ComponentKind.AGENT,
    description="Answers weather and forecast questions",
    permissions=["user"],
), ctx)

# Discover all agents visible to the caller
agents = await registry.discover(ComponentKind.AGENT, ctx)

# Resolve a specific component
entry = await registry.resolve("weather_agent", ctx)
```

### SqliteRegistry

Disk-backed persistence with WAL mode. Survives restarts, single-node only.

```python
from nerva.registry.sqlite import SqliteRegistry

registry = SqliteRegistry(db_path="./data/registry.db")
```

## Health tracking

The registry tracks health per component. The Runtime updates health based on circuit breaker state:

```python
# Mark a handler as degraded after repeated failures
await registry.update("weather_agent", RegistryPatch(health=HealthStatus.DEGRADED))

# Check health before routing
status = await registry.health("weather_agent")
if status == HealthStatus.UNAVAILABLE:
    # Skip this handler
    ...
```

`discover()` automatically filters out disabled and unavailable components.

## Invocation stats

Stats are tracked using an exponential moving average for latency:

```python
entry = await registry.resolve("weather_agent", ctx)
print(entry.stats.total_calls)     # 1,234
print(entry.stats.successes)       # 1,200
print(entry.stats.failures)        # 34
print(entry.stats.avg_duration_ms) # 156.3
```

New observations are blended with a smoothing factor of 0.2, so recent latency is weighted more heavily than historical.

## Partial updates

Use `RegistryPatch` to update specific fields without replacing the entire entry:

```python
from nerva.registry import RegistryPatch, HealthStatus

await registry.update("weather_agent", RegistryPatch(
    description="Updated weather agent with radar support",
    health=HealthStatus.HEALTHY,
    enabled=True,
))
# Only the specified fields are changed; stats and metadata are preserved
```
