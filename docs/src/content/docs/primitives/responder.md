---
title: Responder
description: Format agent output for target channels and tone.
---

The Responder takes raw `AgentResult` from the runtime and formats it for the delivery channel.

## Protocol

```python
class Responder(Protocol):
    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        ...
```

### Value types

```python
@dataclass(frozen=True)
class Channel:
    name: str                    # "api", "slack", "websocket", "sms"
    supports_markdown: bool      # can render **bold**, `code`, etc.
    supports_media: bool         # can display images, files, cards
    max_length: int              # 0 = unlimited

@dataclass
class Response:
    text: str
    channel: Channel
    media: list[str]             # URLs or base64 attachments
    metadata: dict[str, str]     # channel-specific extras
```

Built-in channel presets:

```python
from nerva.responder import API_CHANNEL, WEBSOCKET_CHANNEL

API_CHANNEL       # name="api", markdown=False, media=True, max_length=0
WEBSOCKET_CHANNEL # name="websocket", markdown=True, media=True, max_length=0
```

## Strategies

### PassthroughResponder

Returns the raw agent output with no transformation. Use for APIs and programmatic consumers.

```python
from nerva.responder.passthrough import PassthroughResponder

responder = PassthroughResponder()
response = await responder.format(agent_result, API_CHANNEL, ctx)
# response.text == agent_result.output (untouched)
```

### ToneResponder

Rewrites the output through an LLM to apply personality and tone. The raw content is preserved; only phrasing changes.

```python
from nerva.responder.tone import ToneResponder

responder = ToneResponder(
    llm=my_llm_client,
    system_prompt="You are a friendly travel assistant. Keep responses concise and use casual language.",
)

response = await responder.format(agent_result, WEBSOCKET_CHANNEL, ctx)
# Raw: "Flight LH123 departs at 14:30 from TLV to BER"
# Toned: "Found you a flight! LH123 leaves Tel Aviv at 2:30 PM heading to Berlin."
```

### MultimodalResponder

Enriches output with media attachments, cards, and buttons based on channel capabilities. Falls back to text-only for channels that do not support media.

```python
from nerva.responder.multimodal import MultimodalResponder

responder = MultimodalResponder(
    media_resolver=my_media_resolver,  # resolves media references to URLs
)

response = await responder.format(agent_result, slack_channel, ctx)
# response.media = ["https://cdn.example.com/weather-map-berlin.png"]
# response.metadata = {"blocks": [...]}  # Slack Block Kit
```

## Channel awareness

Define custom channels to control formatting:

```python
sms_channel = Channel(
    name="sms",
    supports_markdown=False,
    supports_media=False,
    max_length=160,
)

slack_channel = Channel(
    name="slack",
    supports_markdown=True,
    supports_media=True,
    max_length=4000,
)

# Responder adapts output to each channel's constraints
sms_response = await responder.format(result, sms_channel, ctx)   # truncated to 160 chars, no markdown
slack_response = await responder.format(result, slack_channel, ctx) # full markdown + images
```

## Streaming

When `ctx.stream` is set, the Responder formats each chunk as it arrives rather than waiting for the full output:

```python
ctx = ExecContext.create(user_id="user_1", stream=my_stream_sink)

# Chunks flow through: Runtime -> Responder.format_chunk() -> ctx.stream -> client
async for chunk in orchestrator.stream("Book a flight", ctx):
    await websocket.send(chunk)
```
