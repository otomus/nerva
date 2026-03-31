/**
 * Multimodal responder — format mixed content for channel-aware delivery.
 *
 * Supports text, image, card, and button content blocks. Degrades gracefully
 * based on channel capabilities (e.g. strips images for text-only channels).
 * Stores structured blocks in response metadata.
 *
 * @module responder/multimodal
 */

import type {
  AgentResult,
  Channel,
  ExecContext,
  Response,
  Responder,
} from "./index.js";
import { createResponse } from "./index.js";

// ---------------------------------------------------------------------------
// Content block types
// ---------------------------------------------------------------------------

/** Discriminator values for content block types. */
export const BlockType = {
  TEXT: "text",
  IMAGE: "image",
  CARD: "card",
  BUTTON: "button",
} as const;

export type BlockType = (typeof BlockType)[keyof typeof BlockType];

/**
 * A plain text content block.
 */
export interface TextBlock {
  readonly type: typeof BlockType.TEXT;
  readonly text: string;
}

/**
 * An image content block.
 */
export interface ImageBlock {
  readonly type: typeof BlockType.IMAGE;
  readonly url: string;
  readonly alt: string;
}

/**
 * A card content block with title, body, and optional image.
 */
export interface CardBlock {
  readonly type: typeof BlockType.CARD;
  readonly title: string;
  readonly body: string;
  readonly imageUrl?: string;
}

/**
 * A button content block.
 */
export interface ButtonBlock {
  readonly type: typeof BlockType.BUTTON;
  readonly label: string;
  readonly action: string;
}

/** Union of all content block types. */
export type ContentBlock = TextBlock | ImageBlock | CardBlock | ButtonBlock;

// ---------------------------------------------------------------------------
// Factory functions
// ---------------------------------------------------------------------------

/**
 * Create a text content block.
 *
 * @param text - The text content.
 * @returns A TextBlock instance.
 */
export function createTextBlock(text: string): TextBlock {
  return { type: BlockType.TEXT, text };
}

/**
 * Create an image content block.
 *
 * @param url - Image URL or base64 data URI.
 * @param alt - Alt text for accessibility.
 * @returns An ImageBlock instance.
 */
export function createImageBlock(url: string, alt: string): ImageBlock {
  return { type: BlockType.IMAGE, url, alt };
}

/**
 * Create a card content block.
 *
 * @param title - Card title.
 * @param body - Card body text.
 * @param imageUrl - Optional card image URL.
 * @returns A CardBlock instance.
 */
export function createCardBlock(title: string, body: string, imageUrl?: string): CardBlock {
  return imageUrl !== undefined
    ? { type: BlockType.CARD, title, body, imageUrl }
    : { type: BlockType.CARD, title, body };
}

/**
 * Create a button content block.
 *
 * @param label - Button display text.
 * @param action - Action identifier triggered on click.
 * @returns A ButtonBlock instance.
 */
export function createButtonBlock(label: string, action: string): ButtonBlock {
  return { type: BlockType.BUTTON, label, action };
}

// ---------------------------------------------------------------------------
// MultimodalResponder
// ---------------------------------------------------------------------------

/**
 * Formats mixed content blocks for channel-aware delivery.
 *
 * Takes the agent result's output as the primary text, plus optional
 * content blocks provided at construction time or via the agent's data
 * field. Degrades content based on channel capabilities:
 *
 * - Channels without media support: images are replaced with alt text
 * - Channels without markdown: cards are rendered as plain text
 * - maxLength is enforced on the final text
 *
 * Structured blocks are serialized into response metadata under the
 * `"blocks"` key.
 *
 * @example
 * ```ts
 * const responder = new MultimodalResponder([
 *   createTextBlock("Here are the results:"),
 *   createImageBlock("https://example.com/chart.png", "Sales chart"),
 *   createCardBlock("Summary", "Revenue increased 15%"),
 * ]);
 * const response = await responder.format(agentResult, channel, ctx);
 * ```
 */
export class MultimodalResponder implements Responder {
  private readonly _blocks: readonly ContentBlock[];

  /**
   * @param blocks - Content blocks to include in the response. Defaults to empty.
   */
  constructor(blocks?: readonly ContentBlock[]) {
    this._blocks = blocks ?? [];
  }

  /**
   * Format agent output with multimodal content blocks.
   *
   * Renders blocks to text, applies channel-aware degradation,
   * and stores structured blocks in response metadata.
   *
   * @param output - Raw agent result from the runtime.
   * @param channel - Target delivery channel with capability flags.
   * @param _ctx - Execution context (unused).
   * @returns Response with rendered text and structured metadata.
   */
  async format(
    output: AgentResult,
    channel: Channel,
    _ctx: ExecContext,
  ): Promise<Response> {
    const allBlocks = buildBlockList(output, this._blocks);
    const renderedParts = renderBlocks(allBlocks, channel);
    const media = extractMediaUrls(allBlocks, channel);

    let text = renderedParts.join("\n\n");
    text = applyMaxLength(text, channel.maxLength);

    const metadata: Record<string, string> = {
      blocks: JSON.stringify(allBlocks),
    };

    return createResponse(text, channel, { media, metadata });
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/**
 * Build the full block list from agent output and pre-configured blocks.
 *
 * If the agent output is non-empty, prepends it as a TextBlock.
 *
 * @param output - Agent result containing output text.
 * @param configuredBlocks - Blocks provided at construction time.
 * @returns Combined list of content blocks.
 */
function buildBlockList(
  output: AgentResult,
  configuredBlocks: readonly ContentBlock[],
): ContentBlock[] {
  const blocks: ContentBlock[] = [];

  if (output.output !== "") {
    blocks.push(createTextBlock(output.output));
  }

  blocks.push(...configuredBlocks);
  return blocks;
}

/**
 * Render content blocks to text strings, applying channel degradation.
 *
 * @param blocks - Content blocks to render.
 * @param channel - Channel capabilities for degradation decisions.
 * @returns Array of rendered text parts.
 */
function renderBlocks(blocks: readonly ContentBlock[], channel: Channel): string[] {
  const parts: string[] = [];

  for (const block of blocks) {
    const rendered = renderBlock(block, channel);
    if (rendered !== "") {
      parts.push(rendered);
    }
  }

  return parts;
}

/**
 * Render a single content block to text.
 *
 * @param block - The content block to render.
 * @param channel - Channel capabilities.
 * @returns Rendered text for this block.
 */
function renderBlock(block: ContentBlock, channel: Channel): string {
  switch (block.type) {
    case BlockType.TEXT:
      return block.text;

    case BlockType.IMAGE:
      return renderImageBlock(block, channel);

    case BlockType.CARD:
      return renderCardBlock(block, channel);

    case BlockType.BUTTON:
      return renderButtonBlock(block, channel);
  }
}

/**
 * Render an image block with channel-aware degradation.
 *
 * @param block - Image block to render.
 * @param channel - Channel capabilities.
 * @returns Markdown image or alt-text fallback.
 */
function renderImageBlock(block: ImageBlock, channel: Channel): string {
  if (!channel.supportsMedia) {
    return `[Image: ${block.alt}]`;
  }
  if (channel.supportsMarkdown) {
    return `![${block.alt}](${block.url})`;
  }
  return block.alt;
}

/**
 * Render a card block with channel-aware degradation.
 *
 * @param block - Card block to render.
 * @param channel - Channel capabilities.
 * @returns Formatted card text.
 */
function renderCardBlock(block: CardBlock, channel: Channel): string {
  if (channel.supportsMarkdown) {
    const header = `**${block.title}**`;
    const parts = [header, block.body];
    if (block.imageUrl !== undefined && channel.supportsMedia) {
      parts.push(`![](${block.imageUrl})`);
    }
    return parts.join("\n");
  }
  return `${block.title}: ${block.body}`;
}

/**
 * Render a button block.
 *
 * @param block - Button block to render.
 * @param channel - Channel capabilities.
 * @returns Button text representation.
 */
function renderButtonBlock(block: ButtonBlock, channel: Channel): string {
  if (channel.supportsMarkdown) {
    return `[${block.label}](action:${block.action})`;
  }
  return `[${block.label}]`;
}

/**
 * Extract media URLs from blocks for channels that support media.
 *
 * @param blocks - Content blocks to scan.
 * @param channel - Channel capabilities.
 * @returns Array of media URLs.
 */
function extractMediaUrls(blocks: readonly ContentBlock[], channel: Channel): string[] {
  if (!channel.supportsMedia) return [];

  const urls: string[] = [];
  for (const block of blocks) {
    if (block.type === BlockType.IMAGE) {
      urls.push(block.url);
    }
    if (block.type === BlockType.CARD && block.imageUrl !== undefined) {
      urls.push(block.imageUrl);
    }
  }
  return urls;
}

/**
 * Truncate text to the channel's maxLength if set.
 *
 * @param text - Text to potentially truncate.
 * @param maxLength - Maximum character length (0 = unlimited).
 * @returns Truncated text.
 */
function applyMaxLength(text: string, maxLength: number): string {
  if (maxLength > 0) {
    return text.slice(0, maxLength);
  }
  return text;
}
