//! Thread-safe service registry with health tracking.
//!
//! Stores handler metadata and invocation statistics in a DashMap
//! for lock-free concurrent reads. Used by the router to discover
//! available handlers at classify time.

use dashmap::DashMap;
use std::time::{SystemTime, UNIX_EPOCH};

/// Health status of a registered handler.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HealthStatus {
    /// Handler is operational.
    Healthy,
    /// Handler is degraded but can still serve requests.
    Degraded,
    /// Handler is offline and should not receive traffic.
    Unhealthy,
}

/// Per-handler invocation statistics.
#[derive(Debug, Clone)]
pub struct InvocationStats {
    /// Total number of invocations.
    pub total: u64,
    /// Number of successful invocations.
    pub successes: u64,
    /// Number of failed invocations.
    pub failures: u64,
    /// Unix timestamp (seconds) of the last invocation.
    pub last_invoked_at: u64,
}

impl Default for InvocationStats {
    fn default() -> Self {
        Self {
            total: 0,
            successes: 0,
            failures: 0,
            last_invoked_at: 0,
        }
    }
}

/// A single entry in the registry.
#[derive(Debug, Clone)]
pub struct RegistryEntry {
    /// Handler name (unique key).
    pub name: String,
    /// Human-readable description.
    pub description: String,
    /// Current health status.
    pub health: HealthStatus,
    /// Invocation statistics.
    pub stats: InvocationStats,
}

/// Thread-safe handler registry backed by DashMap.
pub struct Registry {
    entries: DashMap<String, RegistryEntry>,
}

impl Registry {
    /// Creates an empty registry.
    pub fn new() -> Self {
        Self {
            entries: DashMap::new(),
        }
    }

    /// Registers a handler. Overwrites any existing entry with the same name.
    pub fn register(&self, name: String, description: String) {
        self.entries.insert(
            name.clone(),
            RegistryEntry {
                name,
                description,
                health: HealthStatus::Healthy,
                stats: InvocationStats::default(),
            },
        );
    }

    /// Removes a handler by name. Returns true if it existed.
    pub fn deregister(&self, name: &str) -> bool {
        self.entries.remove(name).is_some()
    }

    /// Returns a snapshot of the entry for the given name, or None.
    pub fn get(&self, name: &str) -> Option<RegistryEntry> {
        self.entries.get(name).map(|e| e.value().clone())
    }

    /// Returns the number of registered handlers.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Returns true if the registry is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Records a successful invocation for the named handler.
    pub fn record_success(&self, name: &str) {
        if let Some(mut entry) = self.entries.get_mut(name) {
            let now = now_secs();
            entry.stats.total += 1;
            entry.stats.successes += 1;
            entry.stats.last_invoked_at = now;
        }
    }

    /// Records a failed invocation for the named handler.
    pub fn record_failure(&self, name: &str) {
        if let Some(mut entry) = self.entries.get_mut(name) {
            let now = now_secs();
            entry.stats.total += 1;
            entry.stats.failures += 1;
            entry.stats.last_invoked_at = now;
        }
    }

    /// Updates the health status of a handler.
    pub fn set_health(&self, name: &str, health: HealthStatus) {
        if let Some(mut entry) = self.entries.get_mut(name) {
            entry.health = health;
        }
    }

    /// Returns names of all registered handlers.
    pub fn names(&self) -> Vec<String> {
        self.entries.iter().map(|e| e.key().clone()).collect()
    }
}

impl Default for Registry {
    fn default() -> Self {
        Self::new()
    }
}

/// Returns the current Unix timestamp in seconds.
fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_and_get() {
        let reg = Registry::new();
        reg.register("handler_a".into(), "does A".into());
        let entry = reg.get("handler_a").unwrap();
        assert_eq!(entry.name, "handler_a");
        assert_eq!(entry.description, "does A");
        assert_eq!(entry.health, HealthStatus::Healthy);
    }

    #[test]
    fn deregister_removes_entry() {
        let reg = Registry::new();
        reg.register("h".into(), "d".into());
        assert!(reg.deregister("h"));
        assert!(reg.get("h").is_none());
    }

    #[test]
    fn record_success_increments_stats() {
        let reg = Registry::new();
        reg.register("h".into(), "d".into());
        reg.record_success("h");
        reg.record_success("h");
        let entry = reg.get("h").unwrap();
        assert_eq!(entry.stats.total, 2);
        assert_eq!(entry.stats.successes, 2);
        assert_eq!(entry.stats.failures, 0);
    }

    #[test]
    fn record_failure_increments_stats() {
        let reg = Registry::new();
        reg.register("h".into(), "d".into());
        reg.record_failure("h");
        let entry = reg.get("h").unwrap();
        assert_eq!(entry.stats.total, 1);
        assert_eq!(entry.stats.failures, 1);
    }

    #[test]
    fn missing_handler_operations_are_no_ops() {
        let reg = Registry::new();
        reg.record_success("missing");
        reg.record_failure("missing");
        reg.set_health("missing", HealthStatus::Unhealthy);
        assert!(!reg.deregister("missing"));
    }
}
