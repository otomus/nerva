"""Tests for MCPToolManager — N-173.

Covers tool discovery, tool calling, permission filtering, armor/sandbox
integration, result truncation, and error handling. Mocks the subprocess
connection layer to avoid real process spawning.
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
    ArmorPolicy,
    MCPConnection,
    MCPConnectionPool,
    MCPProtocolError,
    MCPServerConfig,
    MCPToolManager,
    _build_request,
    _check_armor,
    _extract_tool_output,
    _parse_response,
    _truncate_result,
)

from tests.conftest import make_ctx


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestBuildRequest:
    """Tests for _build_request JSON-RPC serialization."""

    def test_basic_request(self) -> None:
        """Builds a valid JSON-RPC 2.0 request with newline terminator."""
        raw = _build_request("tools/list", 1)
        data = json.loads(raw)
        assert data["jsonrpc"] == "2.0"
        assert data["method"] == "tools/list"
        assert data["id"] == 1
        assert raw.endswith(b"\n")

    def test_request_with_params(self) -> None:
        """Includes params in the request when provided."""
        raw = _build_request("tools/call", 2, {"name": "search", "arguments": {}})
        data = json.loads(raw)
        assert data["params"]["name"] == "search"

    def test_request_without_params(self) -> None:
        """Omits params key when None."""
        raw = _build_request("tools/list", 3)
        data = json.loads(raw)
        assert "params" not in data


class TestParseResponse:
    """Tests for _parse_response JSON-RPC validation."""

    def test_valid_response(self) -> None:
        """Parses a well-formed response and extracts result."""
        response = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}).encode()
        result = _parse_response(response, 1)
        assert result == {"tools": []}

    def test_id_mismatch_raises(self) -> None:
        """Mismatched response ID raises MCPProtocolError."""
        response = json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}).encode()
        with pytest.raises(MCPProtocolError, match="mismatch"):
            _parse_response(response, 1)

    def test_error_response_raises(self) -> None:
        """An error field in the response raises MCPProtocolError."""
        response = json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32600, "message": "Invalid Request"}
        }).encode()
        with pytest.raises(MCPProtocolError, match="Invalid Request"):
            _parse_response(response, 1)

    def test_malformed_json_raises(self) -> None:
        """Non-JSON bytes raise MCPProtocolError."""
        with pytest.raises(MCPProtocolError, match="Invalid JSON"):
            _parse_response(b"not json", 1)

    def test_non_object_raises(self) -> None:
        """A JSON array raises MCPProtocolError."""
        with pytest.raises(MCPProtocolError, match="Expected JSON object"):
            _parse_response(b"[1,2,3]", 1)

    def test_missing_result_returns_empty_dict(self) -> None:
        """Missing result field returns empty dict."""
        response = json.dumps({"jsonrpc": "2.0", "id": 1}).encode()
        result = _parse_response(response, 1)
        assert result == {}

    def test_error_as_string(self) -> None:
        """A non-dict error value still raises MCPProtocolError."""
        response = json.dumps({
            "jsonrpc": "2.0", "id": 1, "error": "something broke"
        }).encode()
        with pytest.raises(MCPProtocolError, match="something broke"):
            _parse_response(response, 1)


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
# Armor checks
# ---------------------------------------------------------------------------

class TestArmorChecks:
    """Tests for _check_armor sandbox policy enforcement."""

    def test_no_path_args_passes(self) -> None:
        """Args without path keys pass armor check."""
        armor = ArmorPolicy()
        assert _check_armor("tool", {"query": "hello"}, armor) is None

    def test_path_arg_denied_when_no_paths_allowed(self) -> None:
        """Path-like args are denied when allowed_paths is empty."""
        armor = ArmorPolicy()
        violation = _check_armor("tool", {"path": "/etc/passwd"}, armor)
        assert violation is not None
        assert "denied" in violation.lower()

    def test_path_arg_allowed_when_under_allowed_path(self) -> None:
        """Path args under an allowed prefix pass."""
        armor = ArmorPolicy(allowed_paths=frozenset(["/home/user"]))
        assert _check_armor("tool", {"path": "/home/user/file.txt"}, armor) is None

    def test_path_arg_denied_when_outside_allowed_path(self) -> None:
        """Path args outside allowed prefixes are denied."""
        armor = ArmorPolicy(allowed_paths=frozenset(["/home/user"]))
        violation = _check_armor("tool", {"path": "/etc/shadow"}, armor)
        assert violation is not None
        assert "outside" in violation.lower()

    def test_network_arg_denied(self) -> None:
        """Network-indicative args are denied when allow_network is False."""
        armor = ArmorPolicy(allow_network=False)
        violation = _check_armor("tool", {"url": "https://evil.com"}, armor)
        assert violation is not None
        assert "network" in violation.lower()

    def test_network_arg_allowed(self) -> None:
        """Network args pass when allow_network is True."""
        armor = ArmorPolicy(allow_network=True)
        assert _check_armor("tool", {"url": "https://ok.com"}, armor) is None


# ---------------------------------------------------------------------------
# MCPToolManager integration
# ---------------------------------------------------------------------------

class TestMCPToolManager:
    """Tests for the MCPToolManager orchestrator."""

    def _make_manager(
        self,
        server_name: str = "test-server",
        armor: ArmorPolicy | None = None,
    ) -> MCPToolManager:
        """Build an MCPToolManager with a single server config.

        Args:
            server_name: Name for the test server.
            armor: Optional armor policy.

        Returns:
            Configured MCPToolManager.
        """
        config = MCPServerConfig(name=server_name, command="echo")
        return MCPToolManager([config], armor=armor)

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
        # Manually register the tool-to-server mapping
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
        """call() returns PERMISSION_DENIED when armor policy is violated."""
        armor = ArmorPolicy(allow_network=False)
        ctx = make_ctx()
        manager = self._make_manager(armor=armor)
        manager._tool_server_map["fetch"] = "test-server"

        result = await manager.call("fetch", {"url": "https://evil.com"}, ctx)
        assert result.status == ToolStatus.PERMISSION_DENIED
        assert "armor" in result.error.lower() or "violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_call_truncates_large_output(self) -> None:
        """call() truncates output exceeding armor max_result_bytes."""
        armor = ArmorPolicy(max_result_bytes=50, allow_network=True)
        ctx = make_ctx()
        manager = self._make_manager(armor=armor)
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
