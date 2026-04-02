"""Tests for MCPToolManager — N-173.

Covers tool discovery, tool calling, permission filtering, armor broker
integration, result truncation, and error handling. Mocks the armored
process layer to avoid real process spawning.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerva.context import ExecContext, Permissions
from nerva.tools import ToolResult, ToolSpec, ToolStatus
from nerva.tools.mcp import (
    DEFAULT_POOL_SIZE,
    MAX_RESULT_BYTES,
    TRUNCATION_SUFFIX,
    MCPArmorViolation,
    MCPConnection,
    MCPConnectionPool,
    MCPProtocolError,
    MCPServerConfig,
    MCPToolManager,
    _extract_tool_output,
    _truncate_result,
)

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestExtractToolOutput:
    """Tests for _extract_tool_output content extraction."""

    def test_text_content_block(self) -> None:
        """Extracts text from the first text content block."""
        result = {"content": [{"type": "text", "text": "hello world"}]}
        assert _extract_tool_output(result) == "hello world"

    def test_no_content_falls_back_to_json(self) -> None:
        """Missing content key falls back to JSON serialization."""
        result = {"something": "else"}
        output = _extract_tool_output(result)
        assert "something" in output

    def test_empty_content_list(self) -> None:
        """Empty content list falls back to JSON serialization."""
        result = {"content": []}
        output = _extract_tool_output(result)
        assert isinstance(output, str)

    def test_non_text_content_block(self) -> None:
        """Content blocks without type=text are skipped."""
        result = {"content": [{"type": "image", "data": "..."}]}
        output = _extract_tool_output(result)
        # Falls back to JSON
        assert "image" in output

    def test_content_not_a_list(self) -> None:
        """Non-list content falls back to JSON serialization."""
        result = {"content": "just a string"}
        output = _extract_tool_output(result)
        assert isinstance(output, str)


class TestTruncateResult:
    """Tests for _truncate_result size limiting."""

    def test_short_output_unchanged(self) -> None:
        """Output within limits is returned as-is."""
        assert _truncate_result("short", 1000) == "short"

    def test_long_output_truncated(self) -> None:
        """Output exceeding limit is truncated with suffix."""
        long_text = "x" * 1000
        result = _truncate_result(long_text, 100)
        assert result.endswith(TRUNCATION_SUFFIX)
        assert len(result.encode("utf-8")) <= 100 + len(TRUNCATION_SUFFIX.encode("utf-8"))

    def test_zero_max_bytes(self) -> None:
        """Zero max_bytes truncates everything, just returns suffix."""
        result = _truncate_result("hello", 0)
        assert TRUNCATION_SUFFIX in result

    def test_exact_boundary(self) -> None:
        """Output exactly at the limit is not truncated."""
        text = "a" * 50
        result = _truncate_result(text, 50)
        assert result == text

    def test_unicode_safe_truncation(self) -> None:
        """Multi-byte characters don't produce invalid UTF-8 on truncation."""
        # Each emoji is 4 bytes in UTF-8
        text = "\U0001f600" * 50  # 200 bytes
        result = _truncate_result(text, 50)
        # Should be valid UTF-8
        result.encode("utf-8")
        assert result.endswith(TRUNCATION_SUFFIX)


# ---------------------------------------------------------------------------
# MCPToolManager integration
# ---------------------------------------------------------------------------


class TestMCPToolManager:
    """Tests for the MCPToolManager orchestrator."""

    def _make_manager(
        self,
        server_name: str = "test-server",
        armor: str | None = None,
        max_result_bytes: int = MAX_RESULT_BYTES,
    ) -> MCPToolManager:
        """Build an MCPToolManager with a single server config.

        Args:
            server_name: Name for the test server.
            armor: Optional path to armor.json.
            max_result_bytes: Result size limit.

        Returns:
            Configured MCPToolManager.
        """
        config = MCPServerConfig(
            name=server_name,
            command="echo",
            armor=armor,
            max_result_bytes=max_result_bytes,
        )
        return MCPToolManager([config])

    @pytest.mark.asyncio
    async def test_discover_returns_tools(self) -> None:
        """discover() returns tool specs from a mocked server."""
        ctx = make_ctx()
        manager = self._make_manager()

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.list_tools = AsyncMock(return_value=[
            ToolSpec(name="search", description="Search things"),
        ])

        with patch.object(manager._pool, "get", return_value=mock_conn):
            tools = await manager.discover(ctx)

        assert len(tools) == 1
        assert tools[0].name == "search"

    @pytest.mark.asyncio
    async def test_discover_empty_server(self) -> None:
        """discover() returns empty list when server has no tools."""
        ctx = make_ctx()
        manager = self._make_manager()

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.list_tools = AsyncMock(return_value=[])

        with patch.object(manager._pool, "get", return_value=mock_conn):
            tools = await manager.discover(ctx)

        assert tools == []

    @pytest.mark.asyncio
    async def test_discover_server_error_returns_empty(self) -> None:
        """discover() returns empty list when server raises an error."""
        ctx = make_ctx()
        manager = self._make_manager()

        with patch.object(manager._pool, "get", side_effect=MCPProtocolError("dead")):
            tools = await manager.discover(ctx)

        assert tools == []

    @pytest.mark.asyncio
    async def test_discover_timeout_returns_empty(self) -> None:
        """discover() returns empty list on connection timeout."""
        ctx = make_ctx()
        manager = self._make_manager()

        with patch.object(manager._pool, "get", side_effect=asyncio.TimeoutError()):
            tools = await manager.discover(ctx)

        assert tools == []

    @pytest.mark.asyncio
    async def test_discover_filters_by_permissions(self) -> None:
        """discover() excludes tools the caller cannot use."""
        ctx = make_ctx(allowed_tools=frozenset(["allowed_tool"]))
        manager = self._make_manager()

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.list_tools = AsyncMock(return_value=[
            ToolSpec(name="allowed_tool", description="ok"),
            ToolSpec(name="blocked_tool", description="no"),
        ])

        with patch.object(manager._pool, "get", return_value=mock_conn):
            tools = await manager.discover(ctx)

        names = [t.name for t in tools]
        assert "allowed_tool" in names
        assert "blocked_tool" not in names

    @pytest.mark.asyncio
    async def test_call_permission_denied(self) -> None:
        """call() returns PERMISSION_DENIED when tool is not in allowlist."""
        ctx = make_ctx(allowed_tools=frozenset(["other_tool"]))
        manager = self._make_manager()
        result = await manager.call("blocked_tool", {}, ctx)
        assert result.status == ToolStatus.PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_call_not_found(self) -> None:
        """call() returns NOT_FOUND for an unknown tool."""
        ctx = make_ctx()
        manager = self._make_manager()
        result = await manager.call("nonexistent", {}, ctx)
        assert result.status == ToolStatus.NOT_FOUND

    @pytest.mark.asyncio
    async def test_call_success(self) -> None:
        """call() returns SUCCESS with tool output."""
        ctx = make_ctx()
        manager = self._make_manager()
        manager._tool_server_map["search"] = "test-server"

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.call_tool = AsyncMock(return_value="search results here")

        with patch.object(manager._pool, "get", return_value=mock_conn):
            result = await manager.call("search", {"query": "cats"}, ctx)

        assert result.status == ToolStatus.SUCCESS
        assert "search results" in result.output

    @pytest.mark.asyncio
    async def test_call_timeout(self) -> None:
        """call() returns TIMEOUT when the server times out."""
        ctx = make_ctx()
        manager = self._make_manager()
        manager._tool_server_map["search"] = "test-server"

        with patch.object(manager._pool, "get", side_effect=asyncio.TimeoutError()):
            result = await manager.call("search", {}, ctx)

        assert result.status == ToolStatus.TIMEOUT

    @pytest.mark.asyncio
    async def test_call_server_error(self) -> None:
        """call() returns ERROR when server raises MCPProtocolError."""
        ctx = make_ctx()
        manager = self._make_manager()
        manager._tool_server_map["search"] = "test-server"

        with patch.object(manager._pool, "get", side_effect=MCPProtocolError("broken")):
            result = await manager.call("search", {}, ctx)

        assert result.status == ToolStatus.ERROR

    @pytest.mark.asyncio
    async def test_call_armor_violation(self) -> None:
        """call() returns PERMISSION_DENIED when broker blocks the call."""
        ctx = make_ctx()
        manager = self._make_manager(armor="armor.json")
        manager._tool_server_map["fetch"] = "test-server"

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.call_tool = AsyncMock(
            side_effect=MCPArmorViolation("Armor violation [-32002]: network access denied")
        )

        with patch.object(manager._pool, "get", return_value=mock_conn):
            result = await manager.call("fetch", {"url": "https://evil.com"}, ctx)

        assert result.status == ToolStatus.PERMISSION_DENIED
        assert "armor" in result.error.lower() or "violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_call_truncates_large_output(self) -> None:
        """call() truncates output exceeding max_result_bytes."""
        ctx = make_ctx()
        manager = self._make_manager(max_result_bytes=50)
        manager._tool_server_map["big"] = "test-server"

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.call_tool = AsyncMock(return_value="x" * 500)

        with patch.object(manager._pool, "get", return_value=mock_conn):
            result = await manager.call("big", {}, ctx)

        assert result.status == ToolStatus.SUCCESS
        assert result.output.endswith(TRUNCATION_SUFFIX)

    @pytest.mark.asyncio
    async def test_close_clears_state(self) -> None:
        """close() empties the tool-server mapping."""
        manager = self._make_manager()
        manager._tool_server_map["search"] = "test-server"

        with patch.object(manager._pool, "close_all", new_callable=AsyncMock):
            await manager.close()

        assert len(manager._tool_server_map) == 0

    @pytest.mark.asyncio
    async def test_duration_ms_populated(self) -> None:
        """call() result includes a positive duration_ms."""
        ctx = make_ctx()
        manager = self._make_manager()
        manager._tool_server_map["t"] = "test-server"

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.call_tool = AsyncMock(return_value="ok")

        with patch.object(manager._pool, "get", return_value=mock_conn):
            result = await manager.call("t", {}, ctx)

        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_call_path_violation_from_broker(self) -> None:
        """call() returns PERMISSION_DENIED on PATH_VIOLATION from broker."""
        ctx = make_ctx()
        manager = self._make_manager(armor="armor.json")
        manager._tool_server_map["read_file"] = "test-server"

        mock_conn = AsyncMock()
        mock_conn.is_connected = True
        mock_conn.call_tool = AsyncMock(
            side_effect=MCPArmorViolation("Armor violation [-32001]: path /etc/passwd blocked")
        )

        with patch.object(manager._pool, "get", return_value=mock_conn):
            result = await manager.call("read_file", {"path": "/etc/passwd"}, ctx)

        assert result.status == ToolStatus.PERMISSION_DENIED
        assert "/etc/passwd" in result.error


# ---------------------------------------------------------------------------
# MCPConnectionPool
# ---------------------------------------------------------------------------


class TestMCPConnectionPool:
    """Tests for the LRU connection pool."""

    @pytest.mark.asyncio
    async def test_pool_evicts_lru(self) -> None:
        """Pool evicts the least-recently-used connection when full."""
        pool = MCPConnectionPool(max_size=2)

        configs = [
            MCPServerConfig(name=f"s{i}", command="echo")
            for i in range(3)
        ]

        # Mock _create_connection to return fake connections
        created: list[str] = []

        async def _fake_create(config: MCPServerConfig) -> MCPConnection:
            """Track which connections are created."""
            created.append(config.name)
            conn = MagicMock(spec=MCPConnection)
            conn.is_connected = True
            conn.close = AsyncMock()
            return conn

        pool._create_connection = _fake_create  # type: ignore[assignment]

        await pool.get(configs[0])
        await pool.get(configs[1])
        # Pool is now full; adding a third should evict s0
        await pool.get(configs[2])

        assert "s0" not in pool._connections
        assert "s2" in pool._connections

    @pytest.mark.asyncio
    async def test_pool_reuses_existing(self) -> None:
        """Getting the same server twice returns the same connection."""
        pool = MCPConnectionPool(max_size=5)
        config = MCPServerConfig(name="reuse", command="echo")

        mock_conn = MagicMock(spec=MCPConnection)
        mock_conn.is_connected = True
        mock_conn.close = AsyncMock()

        async def _fake_create(cfg: MCPServerConfig) -> MCPConnection:
            """Return the mock connection."""
            return mock_conn

        pool._create_connection = _fake_create  # type: ignore[assignment]

        conn1 = await pool.get(config)
        conn2 = await pool.get(config)
        assert conn1 is conn2


# ---------------------------------------------------------------------------
# MCPConnection broker response parsing
# ---------------------------------------------------------------------------


class TestMCPConnectionBrokerResponse:
    """Tests for MCPConnection._parse_broker_response."""

    def _make_connection(self) -> MCPConnection:
        """Create an MCPConnection for testing response parsing."""
        config = MCPServerConfig(name="test", command="echo")
        return MCPConnection(config)

    def test_valid_response(self) -> None:
        """Extracts result from a well-formed response."""
        conn = self._make_connection()
        response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        result = conn._parse_broker_response(response, 1)
        assert result == {"tools": []}

    def test_id_mismatch_raises(self) -> None:
        """Mismatched response ID raises MCPProtocolError."""
        conn = self._make_connection()
        response = {"jsonrpc": "2.0", "id": 99, "result": {}}
        with pytest.raises(MCPProtocolError, match="mismatch"):
            conn._parse_broker_response(response, 1)

    def test_generic_error_raises_protocol_error(self) -> None:
        """A non-armor error raises MCPProtocolError."""
        conn = self._make_connection()
        response = {
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
        with pytest.raises(MCPProtocolError, match="Invalid Request"):
            conn._parse_broker_response(response, 1)

    def test_path_violation_raises_armor_violation(self) -> None:
        """PATH_VIOLATION error code raises MCPArmorViolation."""
        conn = self._make_connection()
        response = {
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32001, "message": "path /etc/passwd blocked"},
        }
        with pytest.raises(MCPArmorViolation, match="path /etc/passwd"):
            conn._parse_broker_response(response, 1)

    def test_network_violation_raises_armor_violation(self) -> None:
        """NETWORK_VIOLATION error code raises MCPArmorViolation."""
        conn = self._make_connection()
        response = {
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32002, "message": "network access denied"},
        }
        with pytest.raises(MCPArmorViolation, match="network access denied"):
            conn._parse_broker_response(response, 1)

    def test_secret_blocked_raises_armor_violation(self) -> None:
        """SECRET_BLOCKED error code raises MCPArmorViolation."""
        conn = self._make_connection()
        response = {
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32004, "message": "env var AWS_SECRET blocked"},
        }
        with pytest.raises(MCPArmorViolation, match="AWS_SECRET"):
            conn._parse_broker_response(response, 1)

    def test_missing_result_returns_empty_dict(self) -> None:
        """Missing result field returns empty dict."""
        conn = self._make_connection()
        response = {"jsonrpc": "2.0", "id": 1}
        result = conn._parse_broker_response(response, 1)
        assert result == {}

    def test_error_as_string(self) -> None:
        """A non-dict error value still raises MCPProtocolError."""
        conn = self._make_connection()
        response = {"jsonrpc": "2.0", "id": 1, "error": "something broke"}
        with pytest.raises(MCPProtocolError, match="something broke"):
            conn._parse_broker_response(response, 1)
