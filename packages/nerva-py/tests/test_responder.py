"""Tests for PassthroughResponder (N-176)."""

from __future__ import annotations

import pytest

from nerva.responder import Channel, Response
from nerva.responder.passthrough import PassthroughResponder
from nerva.runtime import AgentResult, AgentStatus
from tests.conftest import make_ctx


# ===================================================================
# PassthroughResponder
# ===================================================================


class TestPassthroughResponder:
    """Passthrough returns output text as-is, with optional truncation."""

    @pytest.mark.asyncio
    async def test_returns_output_text_as_is(self):
        responder = PassthroughResponder()
        channel = Channel(name="api")
        output = AgentResult(status=AgentStatus.SUCCESS, output="Hello, world!")
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == "Hello, world!"
        assert response.channel is channel

    @pytest.mark.asyncio
    async def test_truncates_to_max_length(self):
        responder = PassthroughResponder()
        channel = Channel(name="api", max_length=5)
        output = AgentResult(status=AgentStatus.SUCCESS, output="Hello, world!")
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == "Hello"
        assert len(response.text) == 5

    @pytest.mark.asyncio
    async def test_max_length_zero_means_unlimited(self):
        responder = PassthroughResponder()
        channel = Channel(name="api", max_length=0)
        long_text = "x" * 10_000
        output = AgentResult(status=AgentStatus.SUCCESS, output=long_text)
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == long_text

    @pytest.mark.asyncio
    async def test_empty_output(self):
        responder = PassthroughResponder()
        channel = Channel(name="api")
        output = AgentResult(status=AgentStatus.SUCCESS, output="")
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == ""

    @pytest.mark.asyncio
    async def test_output_with_special_characters(self):
        responder = PassthroughResponder()
        channel = Channel(name="api")
        special = "line1\nline2\ttab\0null\"quote'apos\\back"
        output = AgentResult(status=AgentStatus.SUCCESS, output=special)
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == special

    @pytest.mark.asyncio
    async def test_output_with_unicode(self):
        responder = PassthroughResponder()
        channel = Channel(name="api")
        unicode_text = "\U0001f600 \u00e9\u00e8\u00ea \u4f60\u597d"
        output = AgentResult(status=AgentStatus.SUCCESS, output=unicode_text)
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == unicode_text

    @pytest.mark.asyncio
    async def test_max_length_larger_than_output(self):
        responder = PassthroughResponder()
        channel = Channel(name="api", max_length=1000)
        output = AgentResult(status=AgentStatus.SUCCESS, output="short")
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == "short"

    @pytest.mark.asyncio
    async def test_media_and_metadata_are_empty(self):
        responder = PassthroughResponder()
        channel = Channel(name="api")
        output = AgentResult(status=AgentStatus.SUCCESS, output="text")
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.media == []
        assert response.metadata == {}

    @pytest.mark.asyncio
    async def test_error_status_output_still_passed_through(self):
        responder = PassthroughResponder()
        channel = Channel(name="api")
        output = AgentResult(status=AgentStatus.ERROR, output="error details", error="fail")
        ctx = make_ctx()

        response = await responder.format(output, channel, ctx)

        assert response.text == "error details"
