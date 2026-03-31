"""Multimodal responder — structured content blocks for mixed media (N-615).

Supports mixed content types: text, images, cards, buttons, and audio.
Channel-aware: gracefully degrades rich content for channels that lack
support (e.g., buttons become text links for CLI consumers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from nerva.responder import Channel, Response
from nerva.runtime import AgentResult, AgentStatus

if TYPE_CHECKING:
    from nerva.context import ExecContext

__all__ = [
    "ContentType",
    "TextBlock",
    "ImageBlock",
    "CardBlock",
    "ButtonBlock",
    "AudioBlock",
    "ContentBlock",
    "MultimodalResponder",
]

# -- Constants ---------------------------------------------------------------

BUTTON_FALLBACK_PREFIX: str = "["
BUTTON_FALLBACK_SUFFIX: str = "]"
IMAGE_FALLBACK_LABEL: str = "[image]"
AUDIO_FALLBACK_LABEL: str = "[audio]"
CARD_SEPARATOR: str = "\n---\n"


# -- Content types -----------------------------------------------------------


class ContentType(StrEnum):
    """Discriminator for content block types.

    Members:
        TEXT: Plain or markdown text.
        IMAGE: An image (URL or base64).
        CARD: A structured card with title, body, and optional image.
        BUTTON: An interactive button with a label and action URL.
        AUDIO: An audio clip (URL or base64).
    """

    TEXT = "text"
    IMAGE = "image"
    CARD = "card"
    BUTTON = "button"
    AUDIO = "audio"


# -- Content blocks ----------------------------------------------------------


@dataclass(frozen=True)
class TextBlock:
    """A block of plain or markdown text.

    Attributes:
        content: The text content.
    """

    content: str
    type: ContentType = field(default=ContentType.TEXT, init=False)


@dataclass(frozen=True)
class ImageBlock:
    """An image content block.

    Attributes:
        url: Image URL or base64 data URI.
        alt_text: Accessible description of the image.
    """

    url: str
    alt_text: str = ""
    type: ContentType = field(default=ContentType.IMAGE, init=False)


@dataclass(frozen=True)
class CardBlock:
    """A structured card with title, body, and optional image.

    Attributes:
        title: Card heading.
        body: Card body text.
        image_url: Optional image URL displayed in the card.
    """

    title: str
    body: str
    image_url: str | None = None
    type: ContentType = field(default=ContentType.CARD, init=False)


@dataclass(frozen=True)
class ButtonBlock:
    """An interactive button.

    Attributes:
        label: Button display text.
        action_url: URL triggered when the button is pressed.
    """

    label: str
    action_url: str
    type: ContentType = field(default=ContentType.BUTTON, init=False)


@dataclass(frozen=True)
class AudioBlock:
    """An audio content block.

    Attributes:
        url: Audio URL or base64 data URI.
        duration_seconds: Duration of the audio clip in seconds.
    """

    url: str
    duration_seconds: float = 0.0
    type: ContentType = field(default=ContentType.AUDIO, init=False)


ContentBlock = TextBlock | ImageBlock | CardBlock | ButtonBlock | AudioBlock
"""Union of all supported content block types."""


# -- Responder ---------------------------------------------------------------


class MultimodalResponder:
    """Format agent output as structured multimodal content blocks.

    Converts raw agent output into a list of content blocks and serializes
    them into the Response. Degrades gracefully for channels that lack
    media or interactive support.

    Blocks are provided via ``set_blocks()`` or auto-generated from the
    agent's text output.
    """

    def __init__(self) -> None:
        self._blocks: list[ContentBlock] = []

    def set_blocks(self, blocks: list[ContentBlock]) -> None:
        """Set the content blocks to render.

        Args:
            blocks: Ordered list of content blocks.
        """
        self._blocks = list(blocks)

    async def format(
        self, output: AgentResult, channel: Channel, ctx: ExecContext
    ) -> Response:
        """Format agent output with multimodal content blocks.

        If no blocks have been set, wraps the agent's text output in a
        single TextBlock. Degrades blocks for channels that lack media
        or interactive support.

        Args:
            output: Raw agent result from the runtime.
            channel: Target delivery channel.
            ctx: Execution context (unused by this responder).

        Returns:
            Response with text and structured metadata.
        """
        blocks = self._blocks if self._blocks else _default_blocks(output)
        degraded = _degrade_for_channel(blocks, channel)
        text = _render_text(degraded)
        text = _apply_max_length(text, channel.max_length)

        media = _extract_media_urls(degraded, channel)
        metadata = _build_metadata(degraded)

        return Response(text=text, channel=channel, media=media, metadata=metadata)


# -- Pure helpers ------------------------------------------------------------


def _default_blocks(output: AgentResult) -> list[ContentBlock]:
    """Create a default TextBlock from agent output.

    Args:
        output: Agent result to wrap.

    Returns:
        Single-element list with a TextBlock, or empty if no output.
    """
    text = output.output or output.error or ""
    if not text:
        return []
    return [TextBlock(content=text)]


def _degrade_for_channel(
    blocks: list[ContentBlock], channel: Channel
) -> list[ContentBlock]:
    """Replace unsupported blocks with text fallbacks for the channel.

    Args:
        blocks: Original content blocks.
        channel: Target channel with capability flags.

    Returns:
        Blocks with unsupported types converted to TextBlock fallbacks.
    """
    result: list[ContentBlock] = []
    for block in blocks:
        degraded = _degrade_block(block, channel)
        result.append(degraded)
    return result


def _degrade_block(block: ContentBlock, channel: Channel) -> ContentBlock:
    """Degrade a single block if the channel cannot render it.

    Args:
        block: Content block to check.
        channel: Target channel.

    Returns:
        Original block if supported, or a TextBlock fallback.
    """
    if isinstance(block, TextBlock):
        return block

    if isinstance(block, ImageBlock):
        if not channel.supports_media:
            alt = block.alt_text or IMAGE_FALLBACK_LABEL
            return TextBlock(content=alt)
        return block

    if isinstance(block, AudioBlock):
        if not channel.supports_media:
            return TextBlock(content=AUDIO_FALLBACK_LABEL)
        return block

    if isinstance(block, ButtonBlock):
        if not channel.supports_media:
            return TextBlock(
                content=f"{BUTTON_FALLBACK_PREFIX}{block.label}{BUTTON_FALLBACK_SUFFIX}({block.action_url})"
            )
        return block

    if isinstance(block, CardBlock):
        if not channel.supports_media:
            return _card_to_text(block)
        return block

    return block


def _card_to_text(card: CardBlock) -> TextBlock:
    """Convert a CardBlock to a plain text representation.

    Args:
        card: Card to convert.

    Returns:
        TextBlock with title and body.
    """
    parts = [f"**{card.title}**", card.body]
    if card.image_url:
        parts.append(f"Image: {card.image_url}")
    return TextBlock(content="\n".join(parts))


def _render_text(blocks: list[ContentBlock]) -> str:
    """Render all blocks into a single text string.

    Args:
        blocks: Content blocks (possibly degraded).

    Returns:
        Concatenated text representation.
    """
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append(block.content)
        elif isinstance(block, ImageBlock):
            parts.append(block.alt_text or block.url)
        elif isinstance(block, CardBlock):
            parts.append(f"{block.title}: {block.body}")
        elif isinstance(block, ButtonBlock):
            parts.append(f"[{block.label}]({block.action_url})")
        elif isinstance(block, AudioBlock):
            parts.append(block.url)
    return "\n".join(parts)


def _extract_media_urls(blocks: list[ContentBlock], channel: Channel) -> list[str]:
    """Extract media URLs from blocks when the channel supports media.

    Args:
        blocks: Content blocks.
        channel: Target channel.

    Returns:
        List of media URLs (images, audio).
    """
    if not channel.supports_media:
        return []
    urls: list[str] = []
    for block in blocks:
        if isinstance(block, ImageBlock):
            urls.append(block.url)
        elif isinstance(block, AudioBlock):
            urls.append(block.url)
        elif isinstance(block, CardBlock) and block.image_url:
            urls.append(block.image_url)
    return urls


def _build_metadata(blocks: list[ContentBlock]) -> dict[str, str]:
    """Build metadata summarizing the content block types.

    Args:
        blocks: Content blocks.

    Returns:
        Metadata dict with block count and type summary.
    """
    if not blocks:
        return {}

    type_counts: dict[str, int] = {}
    for block in blocks:
        block_type = block.type.value
        type_counts[block_type] = type_counts.get(block_type, 0) + 1

    return {
        "block_count": str(len(blocks)),
        "block_types": ",".join(sorted(type_counts.keys())),
    }


def _apply_max_length(text: str, max_length: int) -> str:
    """Truncate text to max_length if the channel defines a positive limit.

    Args:
        text: Text to truncate.
        max_length: Maximum character count (0 = unlimited).

    Returns:
        Truncated text.
    """
    if max_length > 0:
        return text[:max_length]
    return text
