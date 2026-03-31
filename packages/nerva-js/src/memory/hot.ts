/**
 * Hot memory — current session conversation and working state (in-memory).
 *
 * Provides a simple in-memory store for conversation messages, scoped by
 * session ID. Suitable for testing and single-process deployments where
 * persistence across restarts is not required.
 *
 * @module memory/hot
 */

import type { Message } from "./index.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum conversation messages per session before oldest messages are pruned. */
export const DEFAULT_MAX_MESSAGES = 100;

// ---------------------------------------------------------------------------
// InMemoryHotMemory
// ---------------------------------------------------------------------------

/**
 * In-memory hot tier for session state.
 *
 * Stores conversation messages in plain arrays, keyed by session ID.
 * When the message count for a session exceeds `maxMessages`, the
 * oldest messages are pruned to stay within the limit.
 *
 * No persistence — data is lost when the process exits.
 */
export class InMemoryHotMemory {
  private readonly maxMessages: number;
  private readonly conversations: Map<string, Message[]> = new Map();

  /**
   * @param maxMessages - Maximum conversation messages per session before pruning.
   */
  constructor(maxMessages: number = DEFAULT_MAX_MESSAGES) {
    this.maxMessages = maxMessages;
  }

  /**
   * Append a message to a session's conversation history.
   *
   * If the conversation exceeds `maxMessages` after insertion,
   * the oldest messages are removed to enforce the limit.
   *
   * @param role - Message role (e.g. "user", "assistant").
   * @param content - Message content text.
   * @param sessionId - Session to store the message under.
   * @throws {@link Error} If role or content is empty or whitespace-only.
   */
  async addMessage(role: string, content: string, sessionId: string): Promise<void> {
    if (!role || !role.trim()) {
      throw new Error("role must be a non-empty string");
    }
    if (!content || !content.trim()) {
      throw new Error("content must be a non-empty string");
    }

    const messages = this.getOrCreateConversation(sessionId);
    messages.push({ role, content });
    this.pruneIfNeeded(sessionId, messages);
  }

  /**
   * Return a copy of the conversation history for a session.
   *
   * @param sessionId - Session to retrieve messages for.
   * @returns List of role/content records, ordered oldest-first.
   *          Returns an empty array if the session has no history.
   */
  async getConversation(sessionId: string): Promise<Message[]> {
    const messages = this.conversations.get(sessionId);
    if (!messages) {
      return [];
    }
    return [...messages];
  }

  /**
   * Remove all messages for a session.
   *
   * @param sessionId - Session to clear.
   */
  async clear(sessionId: string): Promise<void> {
    this.conversations.delete(sessionId);
  }

  /**
   * Get or create the conversation array for a session.
   *
   * @param sessionId - Session to look up.
   * @returns The mutable message array for this session.
   */
  private getOrCreateConversation(sessionId: string): Message[] {
    const existing = this.conversations.get(sessionId);
    if (existing) {
      return existing;
    }
    const messages: Message[] = [];
    this.conversations.set(sessionId, messages);
    return messages;
  }

  /**
   * Remove oldest messages if the session exceeds the limit.
   *
   * @param _sessionId - Session to check (unused, messages passed directly).
   * @param messages - The mutable message array to prune.
   */
  private pruneIfNeeded(_sessionId: string, messages: Message[]): void {
    const overflow = messages.length - this.maxMessages;
    if (overflow > 0) {
      messages.splice(0, overflow);
    }
  }
}
