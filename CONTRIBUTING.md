# Contributing to Nerva

Thank you for your interest in contributing to Nerva. This guide covers setup, testing, and the process for submitting changes.

## Prerequisites

- Python 3.11+
- Node.js 20+
- Go 1.22+
- npm 10+

## Setting Up the Dev Environment

```bash
# Clone the repo
git clone https://github.com/nerva-ai/nerva.git
cd nerva

# Python package
cd packages/nerva-py
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cd ../..

# TypeScript package
cd packages/nerva-js
npm install
cd ../..

# CLI package
cd packages/nerva-cli
npm install
cd ../..

# Go package
cd packages/nerva-go
go mod download
cd ../..
```

## Running Tests

Each package has its own test suite. All tests must pass before submitting a PR.

```bash
# Python
cd packages/nerva-py && python -m pytest tests/ -q

# TypeScript
cd packages/nerva-js && npx vitest run

# CLI
cd packages/nerva-cli && npx vitest run

# Go
cd packages/nerva-go && go test ./...
```

Never skip or ignore failing tests. If a test fails, fix the root cause.

## Adding a New Primitive Strategy

Nerva primitives are protocol-based (Python Protocol / TypeScript interface). To add a new strategy:

1. **Pick the primitive** you are extending (Router, Runtime, Tools, Memory, Responder, Registry, or Policy).

2. **Create the implementation file** in the correct package directory:
   - Python: `packages/nerva-py/nerva/<primitive>/your_strategy.py`
   - TypeScript: `packages/nerva-js/src/<primitive>/your-strategy.ts`
   - Go: `packages/nerva-go/<primitive>/your_strategy.go`

3. **Implement the protocol/interface.** Every method defined in the protocol must be implemented. Do not use `Any`/`any` types.

4. **Write tests** in the corresponding `tests/` directory. Tests must include:
   - Happy path
   - Edge cases (null/empty/malformed inputs)
   - Wrong types
   - Boundary values

5. **Export the strategy** from the package index so consumers can import it.

6. **Update the spec** if your strategy introduces new types or changes the contract. TypeSpec in `spec/` is the source of truth.

## Writing a CLI Plugin

CLI plugins extend `nerva generate` with custom component types.

1. Create a directory with a `nerva-plugin.json` manifest:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "templates": [
    {
      "type": "workflow",
      "files": ["workflow.py.tmpl", "test_workflow.py.tmpl"]
    }
  ]
}
```

2. Add template files alongside the manifest. Templates use `{{key}}` placeholders (same as the built-in templates). Available variables: `name`, `class_name`, `type`, `description`.

3. Install locally for testing:

```bash
nerva plugin install ./path/to/my-plugin
```

4. Verify your new type is available:

```bash
nerva plugin list
nerva generate workflow my-workflow
```

5. To publish, add the `nerva-plugin` keyword to your `package.json` and publish to npm.

## Releasing

Releases are automated via GitHub Actions. Pushing a `v*` tag triggers the full release pipeline.

### Steps

```bash
# 1. Bump versions in all packages that changed
#    - packages/nerva-py/pyproject.toml
#    - packages/nerva-js/package.json
#    - packages/nerva-cli/package.json

# 2. Commit and push
git add -A && git commit -m "chore: bump to vX.Y.Z"
git push origin main

# 3. Tag and push — this triggers the release
git tag vX.Y.Z
git push origin vX.Y.Z
```

### What the pipeline does

| Workflow | Publishes | Auth |
|----------|-----------|------|
| `release-py.yml` | `nerva` to PyPI | Trusted Publisher (OIDC) — configured at pypi.org/manage/project/otomus-nerva/settings/publishing/ |
| `release-js.yml` | `@otomus/nerva` + `@otomus/nerva-cli` to npm | `NPM_TOKEN` secret |
| `release.yml` | GitHub Release with artifacts | GitHub token |

### PyPI Trusted Publisher setup

PyPI uses OpenID Connect for authentication — no API tokens needed. The trusted publisher must be configured at [pypi.org](https://pypi.org/manage/project/otomus-nerva/settings/publishing/) with:

- **Owner**: `otomus`
- **Repository**: `nerva`
- **Workflow**: `release-py.yml`
- **Environment**: `pypi`

If the trusted publisher is not configured, the `release-py.yml` workflow will fail with `invalid-publisher`. Set it up once and all future releases work automatically.

### npm auth

The `NPM_TOKEN` repository secret must be a valid npm automation token with publish access to the `@otomus` scope.

### Idempotency

Both publish workflows tolerate re-publishes (already-published versions are skipped). This means re-running a failed release is safe — it will only publish what's missing.

## Code Style

All code must follow the rules in:

- `~/.claude/rules/clean-code.md` -- applies to all code
- `~/.claude/rules/testing.md` -- applies to all tests

Key principles:

- Functions do one thing, max 20-30 lines
- Max 3 parameters; use options objects beyond that
- No `Any`/`any` types -- ever
- All exported functions, classes, and types must have docstrings/JSDoc
- Guard clauses over deep nesting (max 3 levels)
- Named constants over magic numbers
- Inject dependencies, don't hardcode them
- Prefer immutability: return new values, don't mutate inputs
- No dead code -- VCS is the history

## PR Process

1. Create a branch from `main` with a descriptive name (`feat/router-hybrid`, `fix/memory-ttl`).
2. Make your changes. Keep commits focused and atomic.
3. Ensure all tests pass across all three language packages.
4. Open a pull request against `main`.
5. Include a summary of what changed and why.
6. Link any related issues.
7. Wait for review. Address feedback in new commits (do not force-push).

## Issue Templates

### Bug Report

- Title: Clear, specific description of the bug
- Steps to reproduce
- Expected behavior
- Actual behavior
- Environment (OS, Python/Node/Go version, Nerva version)
- Minimal reproduction code if applicable

### Feature Request

- Title: Short description of the feature
- Problem: What problem does this solve?
- Proposal: How should it work?
- Alternatives considered
- Which primitive(s) are affected?

### Plugin Submission

- Plugin name and npm package link
- What component types it adds
- Example usage
- Test coverage summary
