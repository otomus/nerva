/**
 * Nerva — the programmable nerve runtime for AI agents.
 *
 * Barrel export re-exporting all public types and classes from
 * the core modules.
 *
 * @module nerva
 */

// -- context.ts -----------------------------------------------------------
export {
  type Scope,
  SCOPE_VALUES,
  type Permissions,
  type PermissionsInit,
  createPermissions,
  TokenUsage,
  type Span,
  type Event,
  type StreamSink,
  InMemoryStreamSink,
  type ExecContextCreateOptions,
  ExecContext,
} from "./context.js";

// -- router/index.ts ------------------------------------------------------
export {
  MIN_CONFIDENCE,
  MAX_CONFIDENCE,
  MIN_SCORE,
  MAX_SCORE,
  type HandlerCandidate,
  createHandlerCandidate,
  type IntentResult,
  createIntentResult,
  type IntentRouter,
} from "./router/index.js";

// -- router/rule.ts -------------------------------------------------------
export { type Rule, RuleRouter } from "./router/rule.js";

// -- router/embedding.ts --------------------------------------------------
export {
  type Embedding,
  type EmbedFn,
  type EmbeddingRouterOptions,
  EmbeddingRouter,
  cosineSimilarity,
} from "./router/embedding.js";

// -- router/hybrid.ts -----------------------------------------------------
export {
  type RerankFn,
  type HybridRouterOptions,
  HybridRouter,
} from "./router/hybrid.js";

// -- router/llm.ts --------------------------------------------------------
export {
  type LLMFn,
  LLMRouter,
} from "./router/llm.js";

// -- orchestrator.ts ------------------------------------------------------
export {
  POLICY_ACTION_ROUTE,
  POLICY_ACTION_INVOKE,
  FALLBACK_HANDLER,
  type PolicyAction,
  type PolicyDecision,
  type PolicyEngine,
  type AgentStatus,
  type AgentInput,
  type AgentResult,
  type AgentRuntime,
  type Channel,
  API_CHANNEL,
  type Response,
  type Responder,
  type Memory,
  type MemoryEvent,
  type Registry,
  type ToolManager,
  PolicyDeniedError,
  type MiddlewareStage,
  MIDDLEWARE_STAGES,
  type MiddlewareHandler,
  type MiddlewareErrorHandler,
  DEFAULT_MIDDLEWARE_PRIORITY,
  type OrchestratorOptions,
  Orchestrator,
} from "./orchestrator.js";

// -- middleware/builtins.ts ------------------------------------------------
export {
  type LogFn,
  type RequestLoggerHandlers,
  requestLogger,
  permissionChecker,
  usageTracker,
} from "./middleware/builtins.js";

// -- tracing/otel.ts ------------------------------------------------------
export {
  isOTelAvailable,
  OTelTracer,
} from "./tracing/otel.js";

// -- tracing/cost.ts ------------------------------------------------------
export {
  DEFAULT_COST_PER_1K_TOKENS,
  MODEL_ATTRIBUTE_KEY,
  COST_ATTRIBUTE_KEY,
  type ModelPricing,
  calculateCost,
  lookupModelCost,
  CostTracker,
} from "./tracing/cost.js";

// -- registry/index.ts ----------------------------------------------------
export {
  ComponentKind,
  HealthStatus,
  DURATION_SMOOTHING_FACTOR,
  InvocationStats,
  type RegistryEntry,
  createRegistryEntry,
  type RegistryPatch,
} from "./registry/index.js";

// -- registry/inmemory.ts -------------------------------------------------
export { InMemoryRegistry } from "./registry/inmemory.js";

// -- registry/sqlite.ts ---------------------------------------------------
export { SqliteRegistry } from "./registry/sqlite.js";

// -- policy/index.ts ------------------------------------------------------
export {
  type PolicyAction as PolicyActionFull,
  createPolicyAction,
  type PolicyDecision as PolicyDecisionFull,
  createPolicyDecision,
  ALLOW,
  DENY_NO_REASON,
  type PolicyEngine as PolicyEngineFull,
} from "./policy/index.js";

// -- policy/noop.ts -------------------------------------------------------
export { NoopPolicyEngine } from "./policy/noop.js";

// -- policy/yaml-engine.ts ------------------------------------------------
export {
  type PolicyConfig,
  parsePolicyConfig,
  type YamlPolicyEngineOptions,
  YamlPolicyEngine,
} from "./policy/yaml-engine.js";

// -- policy/decorator.ts --------------------------------------------------
export {
  type AgentPolicyConfig,
  agentPolicy,
  getAgentPolicy,
  resolvePolicy,
  clearRegistry as clearPolicyRegistry,
} from "./policy/decorator.js";

// -- policy/adaptive.ts ---------------------------------------------------
export {
  COST_DISABLED,
  REASON_BUDGET_EXCEEDED,
  REASON_THROTTLED,
  type AdaptivePolicyConfig,
  createAdaptivePolicyConfig,
  AdaptivePolicyEngine,
} from "./policy/adaptive.js";

// -- tools/index.ts -------------------------------------------------------
export {
  ToolStatus,
  type ToolSpec,
  createToolSpec,
  type ToolResult,
  createToolResult,
  type ToolManager as ToolManagerProtocol,
} from "./tools/index.js";

// -- tools/function.ts ----------------------------------------------------
export {
  type ToolFunction,
  FunctionToolManager,
} from "./tools/function.js";

// -- tools/composite.ts ---------------------------------------------------
export {
  CompositeToolManager,
} from "./tools/composite.js";

// -- tools/mcp.ts ---------------------------------------------------------
export {
  DEFAULT_POOL_SIZE,
  DEFAULT_TIMEOUT_MS,
  MAX_RESULT_BYTES,
  type MCPServerConfig,
  type ArmorPolicy,
  MCPProtocolError,
  MCPConnection,
  MCPConnectionPool,
  type MCPToolManagerOptions,
  MCPToolManager,
  truncateResult,
} from "./tools/mcp.js";

// -- tools/streaming.ts ---------------------------------------------------
export {
  TOOL_START_TYPE,
  TOOL_END_TYPE,
  TOOL_ERROR_TYPE,
  StreamingToolManager,
} from "./tools/streaming.js";

// -- runtime/inprocess.ts -------------------------------------------------
export {
  type HandlerFn,
  type StreamingHandlerFn,
  type InProcessConfig,
  InProcessRuntime,
} from "./runtime/inprocess.js";

// -- runtime/streaming.ts -------------------------------------------------
export {
  type StreamChunkType,
  type StreamChunk,
  StreamingRuntime,
  buildChunk,
  serializeChunk,
} from "./runtime/streaming.js";

// -- runtime/container.ts -------------------------------------------------
export {
  type ContainerHandlerConfig,
  type ContainerRuntimeConfig,
  ContainerRuntime,
} from "./runtime/container.js";

// -- responder/passthrough.ts ---------------------------------------------
export {
  PassthroughResponder,
} from "./responder/passthrough.js";

// -- responder/tone.ts ----------------------------------------------------
export {
  type ToneRewriteFn,
  ToneResponder,
} from "./responder/tone.js";

// -- responder/streaming.ts -----------------------------------------------
export {
  type StreamFormat,
  StreamingResponder,
  formatSse,
  formatWebsocket,
  formatRaw,
  formatForChannel,
} from "./responder/streaming.js";

// -- responder/multimodal.ts ----------------------------------------------
export {
  BlockType,
  type TextBlock,
  type ImageBlock,
  type CardBlock,
  type ButtonBlock,
  type ContentBlock,
  createTextBlock,
  createImageBlock,
  createCardBlock,
  createButtonBlock,
  MultimodalResponder,
} from "./responder/multimodal.js";

// -- memory/index.ts ------------------------------------------------------
export {
  MemoryTier,
  type MemoryEvent as MemoryEventFull,
  createMemoryEvent,
  type Message,
  type MemoryContext,
  createEmptyMemoryContext,
  type Memory as MemoryFull,
} from "./memory/index.js";

// -- memory/hot.ts --------------------------------------------------------
export {
  DEFAULT_MAX_MESSAGES,
  InMemoryHotMemory,
} from "./memory/hot.js";

// -- memory/tiered.ts -----------------------------------------------------
export {
  DEFAULT_TOKEN_BUDGET,
  CHARS_PER_TOKEN,
  type WarmTier,
  type ColdTier,
  type TieredMemoryOptions,
  TieredMemory,
  estimateStringTokens,
  estimateStringsTokens,
  estimateMessagesTokens,
} from "./memory/tiered.js";

// -- memory/warm.ts -------------------------------------------------------
export {
  DEFAULT_MAX_EPISODES,
  DEFAULT_MAX_FACTS,
  type WarmMemoryOptions,
  InMemoryWarmMemory,
} from "./memory/warm.js";

// -- memory/cold.ts -------------------------------------------------------
export {
  DEFAULT_MAX_RESULTS,
  type ColdMemoryOptions,
  InMemoryColdMemory,
} from "./memory/cold.js";

// -- contrib/express.ts ---------------------------------------------------
export {
  REQUEST_ID_HEADER as EXPRESS_REQUEST_ID_HEADER,
  AUTHORIZATION_HEADER as EXPRESS_AUTHORIZATION_HEADER,
  NERVA_CTX_KEY as EXPRESS_NERVA_CTX_KEY,
  SSE_CONTENT_TYPE as EXPRESS_SSE_CONTENT_TYPE,
  type NervaMiddlewareConfig,
  nervaMiddleware,
  permissionsFromBearer,
  type SSEHandlerOptions,
  sseHandler,
} from "./contrib/express.js";

// -- testkit/ -------------------------------------------------------------
// Testkit is available as a separate entry point:
//   import { createTestOrchestrator } from "@otomus/nerva/testkit";
// Re-export the barrel for convenience:
export * as testkit from "./testkit/index.js";

// -- contrib/nestjs.ts ----------------------------------------------------
export {
  NERVA_ORCHESTRATOR_TOKEN,
  NERVA_OPTIONS_TOKEN,
  type NervaDynamicModule,
  type NervaModuleOptions,
  NervaModule,
  NervaCtx,
  extractNervaCtx,
  NervaInterceptor,
  type GuardUser,
  permissionsFromGuard,
} from "./contrib/nestjs.js";
