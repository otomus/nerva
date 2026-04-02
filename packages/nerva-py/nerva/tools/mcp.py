"""MCP tool manager — discover and execute tools via MCP protocol servers.

Uses the ``mcparmor`` broker (``otomus-mcp-armor``) for real OS-level
sandboxing of every tool call. The broker enforces filesystem, network,
and environment restrictions declared in an ``armor.json`` manifest.

Features:
- Connection pooling with LRU eviction (N-131)
- Permission filtering via ctx.permissions (N-132)
- OS-level sandboxing via mcparmor broker (N-133)
- Result size limits with truncation (N-134)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from mcparmor import ArmoredProcess, ArmoredProcessError, ArmorErrorCode

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

ARMOR_ERROR_CODES = frozenset({
    ArmorErrorCode.PATH_VIOLATION,
    ArmorErrorCode.NETWORK_VIOLATION,
    ArmorErrorCode.SECRET_BLOCKED,
    ArmorErrorCode.SPAWN_VIOLATION,
    ArmorErrorCode.TIMEOUT,
})
"""Set of JSON-RPC error codes that indicate an armor policy violation."""

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for an MCP server connection.

    Attributes:
        name: Server identifier used for logging and tool namespacing.
        command: Command to launch the server (stdio transport).
        args: Command-line arguments passed to the server process.
        armor: Path to the ``armor.json`` manifest. ``None`` disables sandboxing.
        armor_profile: Profile name override within the manifest.
        timeout_seconds: Timeout for connection setup and individual tool calls.
        max_result_bytes: Maximum bytes in tool output before truncation.
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    armor: str | None = None
    armor_profile: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_result_bytes: int = MAX_RESULT_BYTES


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MCPProtocolError(Exception):
    """Raised when MCP JSON-RPC communication fails or returns an error."""


class MCPArmorViolation(Exception):
    """Raised when a tool call violates the configured armor policy."""


# ---------------------------------------------------------------------------
# MCPConnection — wraps mcparmor.ArmoredProcess
# ---------------------------------------------------------------------------


class MCPConnection:
    """A single connection to an MCP server via the mcparmor broker.

    Wraps ``mcparmor.ArmoredProcess`` in persistent mode, sending
    JSON-RPC messages through the broker for OS-level sandboxing.
    Synchronous ``invoke()`` calls are bridged to async via executor.

    Args:
        config: Server configuration (command, args, armor manifest, timeout).
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._process: ArmoredProcess | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Start the armored MCP server process in persistent mode.

        Raises:
            ArmoredProcessError: If the broker cannot start.
            OSError: If the command is not found.
        """
        cmd = [self._config.command, *self._config.args]
        self._process = ArmoredProcess(
            cmd,
            armor=self._config.armor,
            profile=self._config.armor_profile,
            ready_signal=False,
        )
        self._process.__enter__()
        _log.debug("MCP server '%s' started via armor broker", self._config.name)

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
        """Invoke a tool through the armored broker.

        Args:
            name: Tool name as registered on the server.
            args: Arguments matching the tool's parameter schema.

        Returns:
            Raw string output from the tool.

        Raises:
            MCPProtocolError: If the server returns an error.
            MCPArmorViolation: If the broker blocks the call.
            asyncio.TimeoutError: If the call exceeds the configured timeout.
        """
        result = await self._send("tools/call", {"name": name, "arguments": args})
        return _extract_tool_output(result)

    async def close(self) -> None:
        """Terminate the armored process and release resources."""
        if self._process is None:
            return
        try:
            self._process.__exit__(None, None, None)
        except Exception:
            _log.debug("Error closing armored process for '%s'", self._config.name, exc_info=True)
        finally:
            self._process = None
            _log.debug("MCP server '%s' closed", self._config.name)

    @property
    def is_connected(self) -> bool:
        """Whether the armored process is alive.

        Returns:
            ``True`` if the process is running.
        """
        return self._process is not None and self._process.is_alive()

    # -- Private ------------------------------------------------------------

    async def _send(
        self, method: str, params: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Send a JSON-RPC request through the broker and parse the response.

        Args:
            method: RPC method name (e.g. ``"tools/list"`` or ``"tools/call"``).
            params: Optional method parameters.

        Returns:
            Parsed ``result`` field from the JSON-RPC response.

        Raises:
            MCPProtocolError: If the connection is dead or response is invalid.
            MCPArmorViolation: If the broker returns an armor error code.
            asyncio.TimeoutError: If the server does not respond in time.
        """
        if not self.is_connected:
            raise MCPProtocolError(f"Not connected to MCP server '{self._config.name}'")

        async with self._lock:
            self._request_id += 1
            message = self._build_message(method, self._request_id, params)
            response = await self._invoke_in_executor(message)
            return self._parse_broker_response(response, self._request_id)

    def _build_message(
        self, method: str, request_id: int, params: dict[str, object] | None
    ) -> dict[str, object]:
        """Build a JSON-RPC 2.0 message dict for the broker.

        Args:
            method: RPC method name.
            request_id: Request identifier.
            params: Optional parameters.

        Returns:
            JSON-serializable dict.
        """
        msg: dict[str, object] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
            "id": request_id,
        }
        if params is not None:
            msg["params"] = params
        return msg

    async def _invoke_in_executor(self, message: dict[str, object]) -> dict:
        """Run the synchronous broker invoke in a thread executor.

        Args:
            message: JSON-RPC message dict.

        Returns:
            Parsed JSON-RPC response dict from the broker.

        Raises:
            asyncio.TimeoutError: If the call exceeds timeout.
            ArmoredProcessError: If broker communication fails.
        """
        assert self._process is not None  # noqa: S101 — guarded by is_connected
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(
                None,
                self._process.invoke,
                message,
            ),
            timeout=self._config.timeout_seconds,
        )

    def _parse_broker_response(
        self, response: dict[str, object], expected_id: int
    ) -> dict[str, object]:
        """Validate a broker response and extract the result.

        Args:
            response: Full JSON-RPC response envelope from the broker.
            expected_id: Expected request ID.

        Returns:
            The ``result`` payload.

        Raises:
            MCPProtocolError: If the response is malformed or has a non-armor error.
            MCPArmorViolation: If the error code indicates a policy violation.
        """
        if response.get("id") != expected_id:
            raise MCPProtocolError(
                f"Response ID mismatch: expected {expected_id}, got {response.get('id')}"
            )

        error = response.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            message = (
                error.get("message", str(error)) if isinstance(error, dict) else str(error)
            )
            if code in ARMOR_ERROR_CODES:
                raise MCPArmorViolation(f"Armor violation [{code}]: {message}")
            raise MCPProtocolError(f"MCP server error [{code}]: {message}")

        return response.get("result", {})  # type: ignore[return-value]


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
    a pool of armored MCP server connections and enforces permission
    filtering and result size limits on every operation. OS-level
    sandboxing is delegated to the mcparmor broker.

    Args:
        servers: MCP server configurations to connect to.
        pool_size: Maximum concurrent server connections in the pool.
    """

    def __init__(
        self,
        servers: list[MCPServerConfig],
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
    ) -> None:
        self._servers = {cfg.name: cfg for cfg in servers}
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
        """Execute a tool call with OS-level sandboxing and size limits.

        Locates the server that owns the tool, validates permissions,
        executes the call through the mcparmor broker, and truncates
        oversized output.

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
        except (MCPProtocolError, ArmoredProcessError, OSError, asyncio.TimeoutError) as exc:
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
        """Run the tool call through the broker and build the result.

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
        except MCPArmorViolation as exc:
            return _armor_violation_result(tool, str(exc), start_ms)
        except (MCPProtocolError, ArmoredProcessError, OSError) as exc:
            return _error_result(tool, str(exc), start_ms)

        output = _truncate_result(raw_output, config.max_result_bytes)
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
        violation: Human-readable violation description from the broker.
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
    "MCPConnection",
    "MCPConnectionPool",
    "MCPToolManager",
    "MCPProtocolError",
    "MCPArmorViolation",
    "DEFAULT_POOL_SIZE",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_RESULT_BYTES",
]
