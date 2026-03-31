"""Minimal FastAPI app demonstrating Nerva integration.

Run with:
    uvicorn main:app --reload
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel

from nerva.context import ExecContext
from nerva.contrib.fastapi import (
    NervaMiddleware,
    get_nerva_ctx,
    streaming_response,
)

# -- App setup ---------------------------------------------------------------

app = FastAPI(title="Nerva FastAPI Agent")
app.add_middleware(NervaMiddleware)

# In a real app, build this from your router, runtime, and responder.
orchestrator = None  # type: ignore[assignment]


# -- Request models ----------------------------------------------------------


class ChatRequest(BaseModel):
    """Incoming chat message."""

    message: str


# -- Routes ------------------------------------------------------------------


@app.post("/chat")
async def chat(
    body: ChatRequest,
    ctx: ExecContext = Depends(get_nerva_ctx),
) -> dict[str, str]:
    """Handle a chat message through the Nerva pipeline.

    Args:
        body: The chat request containing the user message.
        ctx: Nerva execution context injected by middleware.

    Returns:
        A dict with the response text.
    """
    response = await orchestrator.handle(body.message, ctx=ctx)
    return {"text": response.text}


@app.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    ctx: ExecContext = Depends(get_nerva_ctx),
):
    """Stream a chat response as Server-Sent Events.

    Args:
        body: The chat request containing the user message.
        ctx: Nerva execution context injected by middleware.

    Returns:
        A StreamingResponse with SSE-formatted chunks.
    """
    return await streaming_response(orchestrator, body.message, ctx)
