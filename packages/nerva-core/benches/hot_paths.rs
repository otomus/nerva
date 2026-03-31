//! Criterion benchmarks for nerva-core hot paths.

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use nerva_core::{cosine_similarity, count_tokens, Registry};

/// Generates a deterministic vector of the given dimension.
fn make_vector(dim: usize, seed: f32) -> Vec<f32> {
    (0..dim).map(|i| ((i as f32 + seed) * 0.1).sin()).collect()
}

fn bench_cosine_similarity(c: &mut Criterion) {
    let mut group = c.benchmark_group("cosine_similarity");

    for dim in [384, 768, 1536] {
        let a = make_vector(dim, 1.0);
        let b = make_vector(dim, 2.0);

        group.bench_with_input(
            BenchmarkId::from_parameter(dim),
            &dim,
            |bencher, _| {
                bencher.iter(|| cosine_similarity(black_box(&a), black_box(&b)));
            },
        );
    }

    group.finish();
}

fn bench_registry_lookup(c: &mut Criterion) {
    let mut group = c.benchmark_group("registry_lookup");

    for size in [100, 1000] {
        let registry = Registry::new();
        for i in 0..size {
            registry.register(
                format!("service-{i}"),
                format!("Service number {i}"),
            );
        }

        let target = format!("service-{}", size / 2);

        group.bench_with_input(
            BenchmarkId::from_parameter(size),
            &size,
            |bencher, _| {
                bencher.iter(|| registry.get(black_box(&target)));
            },
        );
    }

    group.finish();
}

fn bench_token_counting(c: &mut Criterion) {
    let short = "Hello, world!";
    let medium = "The quick brown fox jumps over the lazy dog. ".repeat(10);
    let long = "word ".repeat(1000);

    let mut group = c.benchmark_group("token_counting");

    group.bench_function("short_13chars", |b| {
        b.iter(|| count_tokens(black_box(short)));
    });
    group.bench_function("medium_450chars", |b| {
        b.iter(|| count_tokens(black_box(&medium)));
    });
    group.bench_function("long_5000chars", |b| {
        b.iter(|| count_tokens(black_box(&long)));
    });

    group.finish();
}

criterion_group!(
    benches,
    bench_cosine_similarity,
    bench_registry_lookup,
    bench_token_counting,
);
criterion_main!(benches);
