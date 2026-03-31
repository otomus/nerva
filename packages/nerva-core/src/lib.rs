//! nerva-core: High-performance core library for the Nerva framework.
//!
//! Provides hot-path optimizations for similarity search, service registry,
//! policy evaluation, token counting, and JSON schema validation.

pub mod similarity;
pub mod registry;
pub mod policy;
pub mod tokenizer;
pub mod schema;

pub use similarity::{cosine_similarity, dot_product, cosine_rank};
pub use registry::{Registry, RegistryEntry, HealthStatus, InvocationStats};
pub use policy::{PolicyEngine, LayeredEngine, PolicyRule, PolicyDecision};
pub use tokenizer::{count_tokens, truncate_to_tokens};
pub use schema::validate;
