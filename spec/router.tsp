import "@typespec/json-schema";
import "./exec_context.tsp";

using TypeSpec.JsonSchema;

/** Minimum allowed score/confidence value. */
const MIN_SCORE: float64 = 0.0;

/** Maximum allowed score/confidence value. */
const MAX_SCORE: float64 = 1.0;

/**
 * A candidate handler returned by the router.
 *
 * The score indicates how well this handler matches the input,
 * on a scale from 0.0 (no match) to 1.0 (perfect match).
 */
@jsonSchema
model HandlerCandidate {
  /** Handler name (must match a registry entry). */
  name: string;

  /** Match score between 0.0 and 1.0. */
  @minValue(0.0)
  @maxValue(1.0)
  score: float64;

  /** Why this handler was selected (for observability). */
  reason?: string;
}

/**
 * Result of intent classification.
 *
 * Contains the classified intent label, confidence, and a ranked
 * list of handler candidates ordered best-first.
 */
@jsonSchema
model IntentResult {
  /** Classified intent label (e.g. "book_flight"). */
  intent: string;

  /** Classification confidence between 0.0 and 1.0. */
  @minValue(0.0)
  @maxValue(1.0)
  confidence: float64;

  /** Ranked list of handler candidates, best first. */
  handlers: HandlerCandidate[];

  /** Optional per-handler scores for debugging. */
  raw_scores?: Record<float64>;
}

/**
 * Intent router interface — classify a user message and select handlers.
 *
 * Every router strategy implements this contract. The orchestrator
 * calls classify() and uses the result to dispatch to the
 * appropriate handler in the runtime.
 */
@jsonSchema
model IntentRouterContract {
  /** Operation: classify a message and return ranked handler candidates. */
  classify: {
    /** Input parameters for classification. */
    input: {
      /** Raw user message text. */
      message: string;

      /** Execution context carrying permissions, trace, and session metadata. */
      ctx: ExecContext;
    };

    /** Classification result with confidence and ranked handlers. */
    output: IntentResult;
  };
}
