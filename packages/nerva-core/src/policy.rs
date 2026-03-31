//! Policy evaluation engine.
//!
//! Evaluates layered allow/deny rules against an action context.
//! Rules are checked in order — first match wins. If no rule matches,
//! the default decision applies.

use serde::{Deserialize, Serialize};

/// The outcome of a policy evaluation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PolicyDecision {
    /// The action is permitted.
    Allow,
    /// The action is denied.
    Deny,
}

/// A single policy rule that matches against action and resource patterns.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyRule {
    /// Glob pattern for the action (e.g. "tool.*", "invoke.weather").
    pub action: String,
    /// Glob pattern for the resource (e.g. "*", "user:123").
    pub resource: String,
    /// Decision when this rule matches.
    pub decision: PolicyDecision,
}

/// Evaluates a list of policy rules in order, first-match-wins.
pub struct PolicyEngine {
    rules: Vec<PolicyRule>,
    default_decision: PolicyDecision,
}

impl PolicyEngine {
    /// Creates a new engine with the given rules and default decision.
    ///
    /// Rules are evaluated in the order provided. The default decision
    /// applies when no rule matches.
    pub fn new(rules: Vec<PolicyRule>, default_decision: PolicyDecision) -> Self {
        Self {
            rules,
            default_decision,
        }
    }

    /// Evaluates the policy for the given action and resource.
    ///
    /// Returns the decision from the first matching rule, or the
    /// default decision if nothing matches.
    pub fn evaluate(&self, action: &str, resource: &str) -> PolicyDecision {
        for rule in &self.rules {
            if glob_matches(&rule.action, action) && glob_matches(&rule.resource, resource) {
                return rule.decision;
            }
        }
        self.default_decision
    }

    /// Returns the number of rules.
    pub fn rule_count(&self) -> usize {
        self.rules.len()
    }
}

/// Layered engine that composes multiple PolicyEngines.
///
/// Engines are evaluated in order. The first engine that produces
/// a Deny wins. If all produce Allow, the result is Allow.
pub struct LayeredEngine {
    layers: Vec<PolicyEngine>,
}

impl LayeredEngine {
    /// Creates a layered engine from multiple policy engines.
    pub fn new(layers: Vec<PolicyEngine>) -> Self {
        Self { layers }
    }

    /// Evaluates all layers. Returns Deny if any layer denies.
    pub fn evaluate(&self, action: &str, resource: &str) -> PolicyDecision {
        for layer in &self.layers {
            if layer.evaluate(action, resource) == PolicyDecision::Deny {
                return PolicyDecision::Deny;
            }
        }
        PolicyDecision::Allow
    }
}

/// Simple glob matching supporting only `*` as a wildcard.
///
/// `*` matches any sequence of characters. All other characters
/// are compared literally.
fn glob_matches(pattern: &str, value: &str) -> bool {
    if pattern == "*" {
        return true;
    }

    let parts: Vec<&str> = pattern.split('*').collect();
    if parts.len() == 1 {
        return pattern == value;
    }

    let mut pos = 0;
    for (i, part) in parts.iter().enumerate() {
        if part.is_empty() {
            continue;
        }
        match value[pos..].find(part) {
            Some(found) => {
                // First segment must match at the start if pattern doesn't begin with *.
                if i == 0 && found != 0 {
                    return false;
                }
                pos += found + part.len();
            }
            None => return false,
        }
    }

    // If pattern doesn't end with *, the value must end exactly.
    if !pattern.ends_with('*') {
        return value.ends_with(parts.last().unwrap_or(&""));
    }

    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_match_allows() {
        let engine = PolicyEngine::new(
            vec![PolicyRule {
                action: "invoke.weather".into(),
                resource: "*".into(),
                decision: PolicyDecision::Allow,
            }],
            PolicyDecision::Deny,
        );
        assert_eq!(
            engine.evaluate("invoke.weather", "any"),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn wildcard_match() {
        let engine = PolicyEngine::new(
            vec![PolicyRule {
                action: "tool.*".into(),
                resource: "*".into(),
                decision: PolicyDecision::Deny,
            }],
            PolicyDecision::Allow,
        );
        assert_eq!(engine.evaluate("tool.exec", "any"), PolicyDecision::Deny);
    }

    #[test]
    fn default_when_no_match() {
        let engine = PolicyEngine::new(vec![], PolicyDecision::Deny);
        assert_eq!(engine.evaluate("anything", "any"), PolicyDecision::Deny);
    }

    #[test]
    fn first_match_wins() {
        let engine = PolicyEngine::new(
            vec![
                PolicyRule {
                    action: "invoke.*".into(),
                    resource: "*".into(),
                    decision: PolicyDecision::Allow,
                },
                PolicyRule {
                    action: "invoke.*".into(),
                    resource: "*".into(),
                    decision: PolicyDecision::Deny,
                },
            ],
            PolicyDecision::Deny,
        );
        assert_eq!(
            engine.evaluate("invoke.test", "x"),
            PolicyDecision::Allow
        );
    }

    #[test]
    fn layered_engine_any_deny_wins() {
        let allow_layer = PolicyEngine::new(vec![], PolicyDecision::Allow);
        let deny_layer = PolicyEngine::new(
            vec![PolicyRule {
                action: "*".into(),
                resource: "*".into(),
                decision: PolicyDecision::Deny,
            }],
            PolicyDecision::Allow,
        );
        let layered = LayeredEngine::new(vec![allow_layer, deny_layer]);
        assert_eq!(layered.evaluate("anything", "any"), PolicyDecision::Deny);
    }

    #[test]
    fn glob_matches_exact() {
        assert!(glob_matches("hello", "hello"));
        assert!(!glob_matches("hello", "world"));
    }

    #[test]
    fn glob_matches_star() {
        assert!(glob_matches("*", "anything"));
        assert!(glob_matches("tool.*", "tool.exec"));
        assert!(!glob_matches("tool.*", "invoke.exec"));
    }
}
