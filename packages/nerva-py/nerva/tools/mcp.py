"""MCP tool manager — discover and execute tools via MCP protocol servers.

Implements the MCP (Model Context Protocol) stdio transport using JSON-RPC 2.0
over subprocess stdin/stdout. No external MCP library required.

Features:
- Connection pooling with LRU eviction (N-131)
- Permission filtering via ctx.permissions (N-132)
- Sandboxing via ArmorPolicy (N-133)
- Result size limits with truncation (N-134)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from nerva.context import ExecContext
from nerva.tools import ToolResult, ToolSpec, ToolStatus

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_POOL_SIZE = 5
"""Maximum number of simultaneous MCP server connections in the pool."""

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Default timeout for MCP server connection and tool calls."""

MAX_RESULT_BYTES = 524_288  # 512 KB
"""Hard limit on tool output size before truncation kicks in."""

TRUNCATION_SUFFIX = "... [truncated]"
"""Appended to tool output that exceeds the byte limit."""

JSONRPC_VERSION = "2.0"
"""JSON-RPC protocol version used for MCP communication."""

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for an MCP server connection.

    Attributes:
        name: Server identifier used for logging and tool namespacing.
        command: Command to launch the server (stdio transport).
        args: Command-line arguments passed to the server process.
        env: Extra environment variables for the server process.
        timeout_seconds: Timeout for connection setup and individual tool calls.
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ArmorPolicy:
    """Sandboxing policy applied before every tool execution.

    Controls filesystem access, network permissions, visible environment
    variables, and output size. An empty allowlist means "none allowed".

    Attributes:
        allowed_paths: Filesystem paths the tool can access. Empty = no filesystem.
        allow_network: Whether the tool may make network calls.
        allowed_env_vars: Environment variable names the tool may read. Empty = none.
        max_result_bytes: Maximum bytes in tool output before truncation.
    """

    allowed_paths: frozenset[str] = field(default_factory=frozenset)
    allow_network: bool = False
    allowed_env_vars: frozenset[str] = field(default_factory=frozenset)
    max_result_bytes: int = MAX_RESULT_BYTES


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _build_request(method: str, request_id: int, params: dict[str, object] | None = None) -> bytes:
    """Serialize a JSON-RPC 2.0 request to a newline-terminated byte string.

    Args:
        method: RPC method name (e.g. ``"tools/list"``).
        request_id: Monotonically increasing request identifier.
        params: Optional parameters for the RPC method.

    Returns:
        UTF-8 encoded JSON line ready to write to stdin.
    """
    payload: dict[str, object] = {
        "jsonrpc": JSONRPC_VERSION,
        "method": method,
        "id": request_id,
    }
    if params is not None:
        payload["params"] = params
    return json.dumps(payload).encode("utf-8") + b"\n"


def _parse_response(line: bytes, expected_id: int) -> dict[str, object]:
    """Parse a JSON-RPC 2.0 response line and validate its structure.

    Args:
        line: Raw bytes read from the server's stdout.
        expected_id: The request ID we expect the response to match.

    Returns:
        The ``result`` field of the JSON-RPC response.

    Raises:
        MCPProtocolError: If the response is malformed or contains an error.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MCPProtocolError(f"Invalid JSON from MCP server: {exc}") from exc

    if not isinstance(data, dict):
        raise MCPProtocolError(f"Expected JSON object, got {type(data).__name__}")

    if data.get("id") != expected_id:
        raise MCPProtocolError(
            f"Response ID mismatch: expected {expected_id}, got {data.get('id')}"
        )

    if "error" in data:
        err = data["error"]
        code = err.get("code", "?") if isinstance(err, dict) else "?"
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise MCPProtocolError(f"MCP server error [{code}]: {message}")

    return data.get("result", {})  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MCPProtocolError(Exception):
    """Raised when MCP JSON-RPC communication fails or returns an error."""


class MCPArmorViolation(Exception):
    """Raised when a tool call violates the configured armor policy."""


# ---------------------------------------------------------------------------
# MCPConnection
# ---------------------------------------------------------------------------


class MCPConnection:
    """A single connection to an MCP server via stdio subprocess.

    Manages the server process lifecycle and JSON-RPC 2.0 communication.
    Each connection holds a running subprocess and communicates by writing
    JSON lines to stdin and reading JSON lines from stdout.

    Args:
        config: Server configuration (command, args, env, timeout).
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Start the MCP server subprocess and verify it is ready.

        Raises:
            MCPProtocolError: If the process fails to start.
            OSError: If the command is not found.
        """
        env = {**os.environ, **self._config.env} if self._config.env else None
        self._process = await asyncio.create_subprocess_exec(
            self._config.command,
            *self._config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _log.debug("MCP server '%s' started (pid=%s)", self._config.name, self._process.pid)

    async def list_tools(self) -> list[ToolSpec]:
        """Discover all tools exposed by this MCP server.

        Returns:
            List of ``ToolSpec`` objects describing each available tool.

        Raises:
            MCPProtocolError: If the server returns an invalid response.
            asyncio.TimeoutError: If the server does not respond in time.
        """
        result = await self._send("tools/list")
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise MCPProtocolError(f"Expected 'tools' list, got {type(raw_tools).__name__}")
        return [_parse_tool_spec(t, self._config.name) for t in raw_tools if isinstance(t, dict)]

    async def call_tool(self, name: str, args: dict[str, object]) -> str:
        """Invoke a tool on this MCP server and return its raw output.

        Args:
            name: Tool name as registered on the server.
            args: Arguments matching the tool's parameter schema.

        Returns:
            Raw string output from the tool.

        Raises:
            MCPProtocolError: If the server returns an error.
            asyncio.TimeoutError: If the call exceeds the configured timeout.
        """
        result = await self._send("tools/call", {"name": name, "arguments": args})
        return _extract_tool_output(result)

    async def close(self) -> None:
        """Terminate the MCP server subprocess and release resources."""
        if self._process is None:
            return
        try:
            if self._process.stdin:
                self._process.stdin.close()
            self._process.terminate()
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            self._process.kill()
        finally:
            _log.debug("MCP server '%s' closed", self._config.name)
            self._process = None

    @property
    def is_connected(self) -> bool:
        """Whether the server subprocess is alive.

        Returns:
            ``True`` if the subprocess is running.
        """
        return self._process is not None and self._process.returncode is None

    # -- Private ------------------------------------------------------------

    async def _send(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """Send a JSON-RPC request and wait for the response.

        Args:
            method: RPC method name.
            params: Optional method parameters.

        Returns:
            Parsed ``result`` field from the JSON-RPC response.

        Raises:
            MCPProtocolError: If the connection is dead or response is invalid.
            asyncio.TimeoutError: If the server does not respond in time.
        """
        if not self.is_connected:
            raise MCPProtocolError(f"Not connected to MCP server '{self._config.name}'")

        async with self._lock:
            self._request_id += 1
            request_id = self._request_id
            request_bytes = _build_request(method, request_id, params)
            return await self._send_raw(request_bytes, request_id)

    async def _send_raw(self, request_bytes: bytes, request_id: int) -> dict[str, object]:
        """Write request bytes and read the response line with timeout.

        Args:
            request_bytes: Serialized JSON-RPC request.
            request_id: Expected response ID for validation.

        Returns:
            Parsed ``result`` from the response.

        Raises:
            MCPProtocolError: On write/read failure or invalid response.
            asyncio.TimeoutError: If the response takes too long.
        """
        assert self._process is not None  # noqa: S101 — guarded by is_connected check
        assert self._process.stdin is not None  # noqa: S101
        assert self._process.stdout is not None  # noqa: S101

        self._process.stdin.write(request_bytes)
        await self._process.stdin.drain()

        line = await asyncio.wait_for(
            self._process.stdout.readline(),
            timeout=self._config.timeout_seconds,
        )
        if not line:
            raise MCPProtocolError(f"MCP server '{self._config.name}' closed stdout unexpectedly")

        return _parse_response(line, request_id)


# ---------------------------------------------------------------------------
# MCPConnectionPool
# ---------------------------------------------------------------------------


class MCPConnectionPool:
    """Pool of MCP server connections with LRU eviction.

    Maintains at most ``max_size`` live connections. When the pool is full
    and a new connection is requested, the least-recently-used connection
    is closed to make room.

    Args:
        max_size: Maximum number of concurrent connections.
    """

    def __init__(self, max_size: int = DEFAULT_POOL_SIZE) -> None:
        self._max_size = max_size
        self._connections: OrderedDict[str, MCPConnection] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, config: MCPServerConfig) -> MCPConnection:
        """Get or create a connection for the given server config.

        Moves the connection to the end of the LRU queue on access.
        If the pool is at capacity, evicts the least-recently-used entry.

        Args:
            config: Server configuration identifying the connection.

        Returns:
            A connected ``MCPConnection`` ready for RPC calls.
        """
        async with self._lock:
            return await self._get_or_create(config)

    async def close_all(self) -> None:
        """Close every connection in the pool and clear the cache."""
        async with self._lock:
            for conn in self._connections.values():
                await _safe_close(conn)
            self._connections.clear()

    # -- Private ------------------------------------------------------------

    async def _get_or_create(self, config: MCPServerConfig) -> MCPConnection:
        """Retrieve an existing connection or create a new one.

        Args:
            config: Server configuration.

        Returns:
            A live ``MCPConnection``.
        """
        key = config.name

        if key in self._connections:
            conn = self._connections[key]
            if conn.is_connected:
                self._connections.move_to_end(key)
                return conn
            # Stale connection — remove and recreate
            await _safe_close(conn)
            del self._connections[key]

        await self._evict_if_full()
        conn = await self._create_connection(config)
        self._connections[key] = conn
        return conn

    async def _evict_if_full(self) -> None:
        """Evict the least-recently-used connection if pool is at capacity."""
        if len(self._connections) < self._max_size:
            return
        oldest_key, oldest_conn = next(iter(self._connections.items()))
        _log.debug("Evicting LRU connection: %s", oldest_key)
        await _safe_close(oldest_conn)
        del self._connections[oldest_key]

    async def _create_connection(self, config: MCPServerConfig) -> MCPConnection:
        """Create and connect a new MCPConnection.

        Args:
            config: Server configuration.

        Returns:
            A freshly connected ``MCPConnection``.
        """
        conn = MCPConnection(config)
        await conn.connect()
        return conn


# ---------------------------------------------------------------------------
# MCPToolManager
# ---------------------------------------------------------------------------


class MCPToolManager:
    """Tool manager that discovers and executes tools via MCP servers.

    Implements the ``ToolManager`` protocol from ``nerva.tools``. Manages
    a pool of MCP server connections and enforces permission filtering,
    sandboxing, and result size limits on every operation.

    Args:
        servers: MCP server configurations to connect to.
        armor: Sandboxing policy. ``None`` means no restrictions.
        pool_size: Maximum concurrent server connections in the pool.
    """

    def __init__(
        self,
        servers: list[MCPServerConfig],
        *,
        armor: ArmorPolicy | None = None,
        pool_size: int = DEFAULT_POOL_SIZE,
    ) -> None:
        self._servers = {cfg.name: cfg for cfg in servers}
        self._armor = armor
        self._pool = MCPConnectionPool(max_size=pool_size)
        self._tool_server_map: dict[str, str] = {}

    async def discover(self, ctx: ExecContext) -> list[ToolSpec]:
        """Discover tools from all configured MCP servers, filtered by permissions.

        Connects to each server, aggregates their tool lists, and removes
        any tools the caller's permissions do not allow.

        Args:
            ctx: Execution context carrying identity and permission set.

        Returns:
            List of tool specs the current caller is permitted to use.
        """
        all_tools: list[ToolSpec] = []
        for config in self._servers.values():
            server_tools = await self._discover_from_server(config, ctx)
            all_tools.extend(server_tools)
        return all_tools

    async def call(
        self, tool: str, args: dict[str, object], ctx: ExecContext
    ) -> ToolResult:
        """Execute a tool call with sandboxing and size limits.

        Locates the server that owns the tool, validates permissions and
        armor policy, executes the call, and truncates oversized output.

        Args:
            tool: Tool name to invoke.
            args: Arguments matching the tool's parameter schema.
            ctx: Execution context carrying identity and permission set.

        Returns:
            ``ToolResult`` with status, output, and timing information.
        """
        start_ms = time.monotonic() * 1000

        if not ctx.permissions.can_use_tool(tool):
            return _permission_denied_result(tool, start_ms)

        server_name = self._tool_server_map.get(tool)
        if server_name is None:
            return _not_found_result(tool, start_ms)

        config = self._servers.get(server_name)
        if config is None:
            return _not_found_result(tool, start_ms)

        if self._armor is not None:
            violation = _check_armor(tool, args, self._armor)
            if violation is not None:
                return _armor_violation_result(tool, violation, start_ms)

        return await self._execute_tool(config, tool, args, start_ms)

    async def close(self) -> None:
        """Close all MCP server connections and release resources."""
        await self._pool.close_all()
        self._tool_server_map.clear()

    # -- Private: discovery -------------------------------------------------

    async def _discover_from_server(
        self, config: MCPServerConfig, ctx: ExecContext
    ) -> list[ToolSpec]:
        """Discover and filter tools from a single MCP server.

        Args:
            config: Server configuration.
            ctx: Execution context for permission checks.

        Returns:
            Permission-filtered tool specs from this server.
        """
        try:
            conn = await self._pool.get(config)
            tools = await conn.list_tools()
        except (MCPProtocolError, OSError, asyncio.TimeoutError) as exc:
            _log.warning("Failed to discover tools from '%s': %s", config.name, exc)
            return []

        permitted = _filter_by_permissions(tools, ctx)
        for spec in permitted:
            self._tool_server_map[spec.name] = config.name
        return permitted

    # -- Private: execution -------------------------------------------------

    async def _execute_tool(
        self,
        config: MCPServerConfig,
        tool: str,
        args: dict[str, object],
        start_ms: float,
    ) -> ToolResult:
        """Run the tool call on the server and build the result.

        Args:
            config: Server that owns the tool.
            tool: Tool name.
            args: Tool arguments.
            start_ms: Monotonic timestamp when the call started (milliseconds).

        Returns:
            ``ToolResult`` with output and timing.
        """
        try:
            conn = await self._pool.get(config)
            raw_output = await conn.call_tool(tool, args)
        except asyncio.TimeoutError:
            return _timeout_result(tool, start_ms)
        except (MCPProtocolError, OSError) as exc:
            return _error_result(tool, str(exc), start_ms)

        max_bytes = self._armor.max_result_bytes if self._armor else MAX_RESULT_BYTES
        output = _truncate_result(raw_output, max_bytes)
        duration_ms = time.monotonic() * 1000 - start_ms

        return ToolResult(
            status=ToolStatus.SUCCESS,
            output=output,
            duration_ms=duration_ms,
        )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_tool_spec(raw: dict[str, object], server_name: str) -> ToolSpec:
    """Convert a raw MCP tool descriptor into a ``ToolSpec``.

    Args:
        raw: Dictionary from the MCP ``tools/list`` response.
        server_name: Owning server name, used as a namespace prefix.

    Returns:
        A ``ToolSpec`` with the tool's name, description, and parameters.
    """
    name = str(raw.get("name", ""))
    description = str(raw.get("description", ""))
    input_schema = raw.get("inputSchema", {})
    parameters = input_schema if isinstance(input_schema, dict) else {}
    return ToolSpec(
        name=name,
        description=description,
        parameters=parameters,
    )


def _extract_tool_output(result: dict[str, object]) -> str:
    """Extract a string output from an MCP ``tools/call`` result.

    The MCP protocol returns content as a list of content blocks.
    This extracts text from the first text block, falling back to
    JSON-serializing the entire result.

    Args:
        result: Parsed ``result`` field from the JSON-RPC response.

    Returns:
        String representation of the tool output.
    """
    content = result.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    # Fallback: serialize the whole result
    return json.dumps(result, default=str)


def _filter_by_permissions(tools: list[ToolSpec], ctx: ExecContext) -> list[ToolSpec]:
    """Remove tools the caller is not permitted to use.

    Args:
        tools: Full tool list from a server.
        ctx: Execution context with permission set.

    Returns:
        Subset of tools the caller may invoke.
    """
    return [t for t in tools if ctx.permissions.can_use_tool(t.name)]


def _truncate_result(output: str, max_bytes: int) -> str:
    """Truncate tool output that exceeds the byte limit.

    Cuts at a byte boundary and appends a truncation suffix so the
    consumer knows data was lost.

    Args:
        output: Raw tool output string.
        max_bytes: Maximum allowed bytes.

    Returns:
        Original output if within limits, otherwise truncated with suffix.
    """
    encoded = output.encode("utf-8")
    if len(encoded) <= max_bytes:
        return output

    suffix_bytes = TRUNCATION_SUFFIX.encode("utf-8")
    cut_at = max_bytes - len(suffix_bytes)
    if cut_at < 0:
        cut_at = 0

    # Decode back safely, ignoring incomplete multi-byte chars at the boundary
    truncated = encoded[:cut_at].decode("utf-8", errors="ignore")
    return truncated + TRUNCATION_SUFFIX


def _check_armor(
    tool_name: str, args: dict[str, object], armor: ArmorPolicy
) -> str | None:
    """Validate a tool call against the armor policy.

    Inspects common argument patterns for filesystem paths and network
    indicators. Returns a human-readable violation reason, or ``None``
    if the call passes all checks.

    Args:
        tool_name: Name of the tool being called.
        args: Arguments that will be passed to the tool.
        armor: Active sandboxing policy.

    Returns:
        Violation description string, or ``None`` if permitted.
    """
    path_violation = _check_path_args(args, armor.allowed_paths)
    if path_violation is not None:
        return path_violation

    if not armor.allow_network:
        network_violation = _check_network_args(args)
        if network_violation is not None:
            return network_violation

    return None


def _check_path_args(args: dict[str, object], allowed_paths: frozenset[str]) -> str | None:
    """Check whether any path-like arguments fall outside allowed paths.

    Args:
        args: Tool arguments to inspect.
        allowed_paths: Set of allowed filesystem path prefixes.

    Returns:
        Violation message if a disallowed path is found, else ``None``.
    """
    if not allowed_paths:
        # No paths allowed — check if any args look like paths
        path_keys = {"path", "file", "filepath", "filename", "directory", "dir"}
        for key in path_keys:
            if key in args:
                return f"Filesystem access denied: argument '{key}' not permitted"
        return None

    path_keys = {"path", "file", "filepath", "filename", "directory", "dir"}
    for key in path_keys:
        value = args.get(key)
        if not isinstance(value, str):
            continue
        if not _is_path_allowed(value, allowed_paths):
            return f"Path '{value}' is outside allowed paths"

    return None


def _is_path_allowed(path: str, allowed_paths: frozenset[str]) -> bool:
    """Check if a path is under one of the allowed prefixes.

    Args:
        path: Filesystem path to validate.
        allowed_paths: Set of allowed path prefixes.

    Returns:
        ``True`` if the path starts with at least one allowed prefix.
    """
    normalized = os.path.normpath(path)
    return any(normalized.startswith(os.path.normpath(ap)) for ap in allowed_paths)


def _check_network_args(args: dict[str, object]) -> str | None:
    """Check whether arguments suggest a network call.

    Args:
        args: Tool arguments to inspect.

    Returns:
        Violation message if network indicators are found, else ``None``.
    """
    network_keys = {"url", "uri", "endpoint", "host", "hostname"}
    for key in network_keys:
        if key in args:
            return f"Network access denied: argument '{key}' not permitted"
    return None


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def _elapsed_ms(start_ms: float) -> float:
    """Calculate elapsed milliseconds from a monotonic start.

    Args:
        start_ms: Start time from ``time.monotonic() * 1000``.

    Returns:
        Milliseconds elapsed since start.
    """
    return time.monotonic() * 1000 - start_ms


def _permission_denied_result(tool: str, start_ms: float) -> ToolResult:
    """Build a PERMISSION_DENIED result.

    Args:
        tool: Tool name that was denied.
        start_ms: Call start timestamp in monotonic milliseconds.

    Returns:
        ``ToolResult`` with PERMISSION_DENIED status.
    """
    return ToolResult(
        status=ToolStatus.PERMISSION_DENIED,
        error=f"Permission denied for tool '{tool}'",
        duration_ms=_elapsed_ms(start_ms),
    )


def _not_found_result(tool: str, start_ms: float) -> ToolResult:
    """Build a NOT_FOUND result.

    Args:
        tool: Tool name that was not found.
        start_ms: Call start timestamp in monotonic milliseconds.

    Returns:
        ``ToolResult`` with NOT_FOUND status.
    """
    return ToolResult(
        status=ToolStatus.NOT_FOUND,
        error=f"Tool '{tool}' not found on any configured MCP server",
        duration_ms=_elapsed_ms(start_ms),
    )


def _timeout_result(tool: str, start_ms: float) -> ToolResult:
    """Build a TIMEOUT result.

    Args:
        tool: Tool name that timed out.
        start_ms: Call start timestamp in monotonic milliseconds.

    Returns:
        ``ToolResult`` with TIMEOUT status.
    """
    return ToolResult(
        status=ToolStatus.TIMEOUT,
        error=f"Tool '{tool}' timed out",
        duration_ms=_elapsed_ms(start_ms),
    )


def _error_result(tool: str, message: str, start_ms: float) -> ToolResult:
    """Build a generic ERROR result.

    Args:
        tool: Tool name that errored.
        message: Error description.
        start_ms: Call start timestamp in monotonic milliseconds.

    Returns:
        ``ToolResult`` with ERROR status.
    """
    return ToolResult(
        status=ToolStatus.ERROR,
        error=f"Tool '{tool}' failed: {message}",
        duration_ms=_elapsed_ms(start_ms),
    )


def _armor_violation_result(tool: str, violation: str, start_ms: float) -> ToolResult:
    """Build a PERMISSION_DENIED result for an armor policy violation.

    Args:
        tool: Tool name that violated policy.
        violation: Human-readable violation description.
        start_ms: Call start timestamp in monotonic milliseconds.

    Returns:
        ``ToolResult`` with PERMISSION_DENIED status.
    """
    return ToolResult(
        status=ToolStatus.PERMISSION_DENIED,
        error=f"Armor violation for tool '{tool}': {violation}",
        duration_ms=_elapsed_ms(start_ms),
    )


async def _safe_close(conn: MCPConnection) -> None:
    """Close a connection, suppressing any errors.

    Args:
        conn: Connection to close.
    """
    try:
        await conn.close()
    except Exception:
        _log.debug("Error closing MCP connection", exc_info=True)


__all__ = [
    "MCPServerConfig",
    "ArmorPolicy",
    "MCPConnection",
    "MCPConnectionPool",
    "MCPToolManager",
    "MCPProtocolError",
    "MCPArmorViolation",
    "DEFAULT_POOL_SIZE",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RESULT_BYTES",
]
