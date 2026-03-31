"""FastAPI API gateway example — demonstrates Nerva with auth, streaming SSE, and Swagger docs."""

import asyncio
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from nerva import ExecContext, Orchestrator
from nerva.memory import TieredMemory
from nerva.policy import NoopPolicyEngine
from nerva.registry import InMemoryRegistry
from nerva.responder import PassthroughResponder
from nerva.router import Rule, RuleRouter
from nerva.tools import FunctionToolManager

# ---------------------------------------------------------------------------
# Auth (mock — replace with real JWT/OAuth)
# ---------------------------------------------------------------------------

API_KEYS = {
    "key_alice": {"user_id": "alice", "role": "admin"},
    "key_bob": {"user_id": "bob", "role": "user"},
}


async def get_current_user(request: Request) -> dict:
    """Extract and validate the API key from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = auth[len("Bearer "):]
    user = API_KEYS.get(token)
    if not user:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return user


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

tools = FunctionToolManager()


@tools.register(name="get_time", description="Returns the current date and time")
async def get_time(_args: dict, _ctx: ExecContext) -> str:
    """Return the current ISO-formatted timestamp."""
    return datetime.now().isoformat()


@tools.register(name="echo", description="Echoes back the input message")
async def echo(args: dict, _ctx: ExecContext) -> str:
    """Echo the message back."""
    return args.get("message", "")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def time_handler(input_text: str, ctx: ExecContext) -> str:
    """Handle time-related queries."""
    result = await tools.call("get_time", {}, ctx)
    return f"The current time is {result.output}"


async def general_handler(input_text: str, _ctx: ExecContext) -> str:
    """Catch-all handler."""
    return f"You said: {input_text}"


HANDLERS = {
    "time": time_handler,
    "general": general_handler,
}

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

router = RuleRouter(rules=[
    Rule(pattern=r"(?i)(time|clock|date|now)", handler="time", description="Time queries"),
    Rule(pattern=r".*", handler="general", description="General catch-all"),
])


class SimpleRuntime:
    """Minimal in-process runtime."""

    async def invoke(self, handler: str, input_data: dict, ctx: ExecContext) -> dict:
        """Invoke a handler by name."""
        fn = HANDLERS.get(handler, general_handler)
        text = await fn(input_data.get("query", ""), ctx)
        return {"text": text, "status": "success"}


orchestrator = Orchestrator(
    router=router,
    runtime=SimpleRuntime(),
    tools=tools,
    memory=TieredMemory(),
    responder=PassthroughResponder(),
    registry=InMemoryRegistry(),
    policy=NoopPolicyEngine(),
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Nerva API Gateway",
    description="Example API gateway using Nerva for agent orchestration.",
    version="0.1.0",
)


class ChatRequest(BaseModel):
    """Incoming chat message."""

    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Agent response."""

    text: str
    user_id: str
    session_id: str | None


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)) -> ChatResponse:
    """Send a message to the agent and receive a response."""
    ctx = ExecContext.create(
        user_id=user["user_id"],
        session_id=req.session_id,
    )
    result = await orchestrator.handle(req.message, ctx)
    return ChatResponse(
        text=result.text,
        user_id=user["user_id"],
        session_id=req.session_id,
    )


@app.get("/chat/stream")
async def chat_stream(
    q: str,
    session_id: str | None = None,
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """Stream agent response as Server-Sent Events."""
    ctx = ExecContext.create(
        user_id=user["user_id"],
        session_id=session_id,
    )

    async def generate():
        # In a real implementation, orchestrator.stream() yields chunks.
        # For this example, we simulate streaming by yielding the full response.
        result = await orchestrator.handle(q, ctx)
        for word in result.text.split():
            yield f"data: {word}\n\n"
            await asyncio.sleep(0.05)
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}
