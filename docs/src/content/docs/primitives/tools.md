---
title: Tools
description: Discover, sandbox, and invoke external tools.
---

The ToolManager discovers tools, enforces permissions, and executes them within sandbox constraints.

## Protocol

```python
class ToolManager(Protocol):
    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        ...

    async def call(self, tool: str, args: dict[str, object], ctx: ExecContext) -> ToolResult:
        ...
```

### Value types

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, object]       # JSON Schema
    required_permissions: frozenset[str]

@dataclass(frozen=True)
class ToolResult:
    status: ToolStatus  # SUCCESS | ERROR | PERMISSION_DENIED | NOT_FOUND | TIMEOUT
    output: str
    error: str | None
    duration_ms: float
```

## Strategies

### FunctionToolManager

Register plain Python/TypeScript functions as tools. Schema is auto-extracted from type hints.

```python
from nerva.tools.function import FunctionToolManager

tools = FunctionToolManager()

@tools.tool("search_flights", "Search for available flights")
async def search_flights(origin: str, destination: str, date: str) -> str:
    # Call your flight API
    return f"Found 3 flights from {origin} to {destination} on {date}"

# Discover available tools (filtered by ctx.permissions)
specs = await tools.discover(ctx)

# Call a tool
result = await tools.call("search_flights", {
    "origin": "TLV",
    "destination": "BER",
    "date": "2025-04-15",
}, ctx)

print(result.status)      # ToolStatus.SUCCESS
print(result.output)       # "Found 3 flights from TLV to BER on 2025-04-15"
print(result.duration_ms)  # 23.4
```

Sync functions are automatically offloaded to a thread via `asyncio.to_thread`.

### MCPToolManager

Connects to MCP (Model Context Protocol) servers. Tools are discovered dynamically, executed in sandboxed server processes.

```python
from nerva.tools.mcp import MCPToolManager

tools = MCPToolManager(
    servers={
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
        "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
    },
    max_connections=5,
)

# Tools from all connected servers
specs = await tools.discover(ctx)
result = await tools.call("read_file", {"path": "/tmp/data.json"}, ctx)
```

### CompositeToolManager

Combines multiple tool sources into a single manager. Handles deduplication and priority.

```python
from nerva.tools.composite import CompositeToolManager

tools = CompositeToolManager(managers=[
    function_tools,  # local functions (highest priority)
    mcp_tools,       # MCP servers (lower priority)
])

# Discovers tools from all sources
specs = await tools.discover(ctx)
```

## Permissions

Tools support two layers of access control:

**Context permissions** -- checked via `ctx.permissions.can_use_tool(name)`:

```python
ctx = ExecContext.create(
    user_id="user_1",
    permissions=Permissions(allowed_tools={"search_flights", "get_weather"}),
)

# Only search_flights and get_weather are returned
specs = await tools.discover(ctx)
```

**Role-based permissions** -- set per tool at registration:

```python
@tools.tool(
    "deploy_service",
    "Deploy a service to production",
    required_permissions=frozenset({"admin", "devops"}),
)
async def deploy_service(service: str) -> str:
    ...
```

Both checks must pass. A `PERMISSION_DENIED` result is returned if either fails.

## Sandboxing

`MCPToolManager` runs tools in isolated server processes with configurable constraints:

- **Process isolation** -- each MCP server runs in its own subprocess
- **Connection pooling** -- LRU pool reuses connections, evicts idle servers
- **Timeout enforcement** -- per-call timeout with clean process termination
- **Result size limits** -- output is truncated if it exceeds the configured maximum
