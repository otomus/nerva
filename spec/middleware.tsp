import "@typespec/json-schema";

using TypeSpec.JsonSchema;

/**
 * Pipeline stages where middleware can hook in.
 *
 * Each stage fires at a specific point in the request lifecycle:
 * - BEFORE_ROUTE: After context creation, before the router picks a handler.
 * - BEFORE_INVOKE: After routing, before the selected handler executes.
 * - AFTER_INVOKE: After the handler returns, before response formatting.
 * - BEFORE_RESPOND: After formatting, before the response is sent to the caller.
 */
@jsonSchema
enum MiddlewareStage {
  before_route: "before_route",
  before_invoke: "before_invoke",
  after_invoke: "after_invoke",
  before_respond: "before_respond",
}
