"""Tests for FunctionToolManager (N-174)."""

from __future__ import annotations

import pytest

from nerva.context import ExecContext, Permissions
from nerva.tools import ToolStatus
from nerva.tools.function import FunctionToolManager, _extract_parameters
from tests.conftest import make_ctx


# ===================================================================
# Registration and discovery
# ===================================================================


class TestFunctionToolRegistration:
    """Register and discover function tools."""

    def test_register_sync_function(self):
        mgr = FunctionToolManager()

        @mgr.tool("add", "Add two numbers")
        def add(a: int, b: int) -> int:
            return a + b

        assert "add" in mgr._tools

    def test_duplicate_name_raises_value_error(self):
        mgr = FunctionToolManager()

        @mgr.tool("calc", "First")
        def calc_v1(x: int) -> int:
            return x

        with pytest.raises(ValueError, match="already registered"):
            @mgr.tool("calc", "Second")
            def calc_v2(x: int) -> int:
                return x * 2

    @pytest.mark.asyncio
    async def test_discover_returns_all_tools(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("a", "Tool A")
        def tool_a() -> str:
            return "a"

        @mgr.tool("b", "Tool B")
        def tool_b() -> str:
            return "b"

        specs = await mgr.discover(ctx)
        names = {s.name for s in specs}
        assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_discover_filters_by_allowed_tools(self):
        mgr = FunctionToolManager()
        ctx = make_ctx(allowed_tools=frozenset({"visible"}))

        @mgr.tool("visible", "Allowed")
        def vis() -> str:
            return "v"

        @mgr.tool("hidden", "Not allowed")
        def hid() -> str:
            return "h"

        specs = await mgr.discover(ctx)
        assert len(specs) == 1
        assert specs[0].name == "visible"

    @pytest.mark.asyncio
    async def test_discover_filters_by_required_roles(self):
        mgr = FunctionToolManager()
        ctx = make_ctx(roles=frozenset({"user"}))

        @mgr.tool("admin_only", "Needs admin", required_permissions=frozenset({"admin"}))
        def admin_tool() -> str:
            return "admin"

        @mgr.tool("public", "No role needed")
        def public_tool() -> str:
            return "public"

        specs = await mgr.discover(ctx)
        assert len(specs) == 1
        assert specs[0].name == "public"


# ===================================================================
# Calling tools
# ===================================================================


class TestFunctionToolCall:
    """Invoke registered tools through call()."""

    @pytest.mark.asyncio
    async def test_call_sync_function(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("add", "Add numbers")
        def add(a: int, b: int) -> int:
            return a + b

        result = await mgr.call("add", {"a": 3, "b": 4}, ctx)
        assert result.status == ToolStatus.SUCCESS
        assert result.output == "7"

    @pytest.mark.asyncio
    async def test_call_async_function(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("greet", "Greet user")
        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        result = await mgr.call("greet", {"name": "Alice"}, ctx)
        assert result.status == ToolStatus.SUCCESS
        assert result.output == "Hello, Alice!"

    @pytest.mark.asyncio
    async def test_call_unknown_tool_returns_not_found(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()
        result = await mgr.call("nonexistent", {}, ctx)
        assert result.status == ToolStatus.NOT_FOUND

    @pytest.mark.asyncio
    async def test_call_without_permission_returns_denied(self):
        mgr = FunctionToolManager()
        ctx = make_ctx(allowed_tools=frozenset({"other"}))

        @mgr.tool("secret", "Restricted tool")
        def secret() -> str:
            return "classified"

        result = await mgr.call("secret", {}, ctx)
        assert result.status == ToolStatus.PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_call_without_required_role_returns_denied(self):
        mgr = FunctionToolManager()
        ctx = make_ctx(roles=frozenset({"user"}))

        @mgr.tool("admin_op", "Admin only", required_permissions=frozenset({"admin"}))
        def admin_op() -> str:
            return "done"

        result = await mgr.call("admin_op", {}, ctx)
        assert result.status == ToolStatus.PERMISSION_DENIED

    @pytest.mark.asyncio
    async def test_call_that_raises_returns_error(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("boom", "Raises an error")
        def boom() -> str:
            raise RuntimeError("kaboom")

        result = await mgr.call("boom", {}, ctx)
        assert result.status == ToolStatus.ERROR
        assert "kaboom" in (result.error or "")

    @pytest.mark.asyncio
    async def test_call_records_duration(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("slow", "Takes time")
        def slow() -> str:
            return "done"

        result = await mgr.call("slow", {}, ctx)
        assert result.duration_ms >= 0


# ===================================================================
# _extract_parameters
# ===================================================================


class TestExtractParameters:
    """Schema extraction from function signatures."""

    def test_typed_params(self):
        def fn(a: int, b: str) -> None:
            pass

        schema = _extract_parameters(fn)
        assert schema["type"] == "object"
        props = schema["properties"]
        assert props["a"]["type"] == "integer"
        assert props["b"]["type"] == "string"
        assert set(schema["required"]) == {"a", "b"}

    def test_param_with_default_not_required(self):
        def fn(a: int, b: str = "default") -> None:
            pass

        schema = _extract_parameters(fn)
        assert schema["required"] == ["a"]

    def test_no_params(self):
        def fn() -> None:
            pass

        schema = _extract_parameters(fn)
        assert schema["properties"] == {}
        assert "required" not in schema

    def test_no_annotations_default_to_string(self):
        def fn(x, y):
            pass

        schema = _extract_parameters(fn)
        assert schema["properties"]["x"]["type"] == "string"
        assert schema["properties"]["y"]["type"] == "string"

    def test_bool_and_float_types(self):
        def fn(flag: bool, amount: float) -> None:
            pass

        schema = _extract_parameters(fn)
        assert schema["properties"]["flag"]["type"] == "boolean"
        assert schema["properties"]["amount"]["type"] == "number"


# ===================================================================
# Edge cases
# ===================================================================


class TestFunctionToolEdgeCases:
    """Boundary and unusual inputs."""

    @pytest.mark.asyncio
    async def test_tool_returning_none(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("noop", "Returns nothing")
        def noop() -> None:
            return None

        result = await mgr.call("noop", {}, ctx)
        assert result.status == ToolStatus.SUCCESS
        assert result.output == "None"

    @pytest.mark.asyncio
    async def test_async_tool_that_raises(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("async_boom", "Async error")
        async def async_boom() -> str:
            raise ValueError("async kaboom")

        result = await mgr.call("async_boom", {}, ctx)
        assert result.status == ToolStatus.ERROR
        assert "async kaboom" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_args_dict(self):
        mgr = FunctionToolManager()
        ctx = make_ctx()

        @mgr.tool("no_args", "No arguments needed")
        def no_args() -> str:
            return "ok"

        result = await mgr.call("no_args", {}, ctx)
        assert result.status == ToolStatus.SUCCESS
