"""Conformance validator — checks Python (and Node.js) models against generated JSON Schema.

Loads test cases from test_cases.json, resolves each referenced schema from
the generated output directory, and validates every valid/invalid instance.

Usage:
    python spec/conformance/validate.py

Requirements:
    pip install jsonschema pyyaml

Exit codes:
    0 — all cases pass
    1 — one or more validation failures
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: pip install pyyaml")

try:
    from jsonschema import Draft202012Validator, ValidationError
except ImportError:
    sys.exit("jsonschema is required: pip install jsonschema")


CONFORMANCE_DIR = Path(__file__).parent
GENERATED_DIR = CONFORMANCE_DIR.parent / "generated"
TEST_CASES_PATH = CONFORMANCE_DIR / "test_cases.json"


def load_schema(schema_filename: str) -> dict:
    """Load a YAML schema file from the generated directory.

    Args:
        schema_filename: Name of the schema file (e.g. "Scope.yaml").

    Returns:
        Parsed schema as a dict.

    Raises:
        FileNotFoundError: If the schema file does not exist.
    """
    schema_path = GENERATED_DIR / schema_filename
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    with open(schema_path) as fh:
        return yaml.safe_load(fh)


def run_case(case: dict) -> list[str]:
    """Run a single conformance test case.

    Validates all 'valid' instances (expecting pass) and all 'invalid'
    instances (expecting failure) against the referenced schema.

    Args:
        case: A test case dict with 'schema', 'valid', and 'invalid' keys.

    Returns:
        List of failure messages. Empty list means the case passed.
    """
    schema_file = case["schema"]
    description = case.get("description", schema_file)
    failures: list[str] = []

    try:
        schema = load_schema(schema_file)
    except FileNotFoundError as exc:
        return [f"[{description}] {exc}"]

    validator = Draft202012Validator(schema)

    for idx, instance in enumerate(case.get("valid", [])):
        errors = list(validator.iter_errors(instance))
        if errors:
            error_messages = "; ".join(e.message for e in errors)
            failures.append(
                f"[{description}] valid[{idx}] rejected: {error_messages} "
                f"(instance: {json.dumps(instance)})"
            )

    for idx, instance in enumerate(case.get("invalid", [])):
        errors = list(validator.iter_errors(instance))
        if not errors:
            failures.append(
                f"[{description}] invalid[{idx}] accepted but should have been rejected "
                f"(instance: {json.dumps(instance)})"
            )

    return failures


def main() -> int:
    """Run all conformance test cases and report results.

    Returns:
        0 if all cases pass, 1 if any fail.
    """
    with open(TEST_CASES_PATH) as fh:
        data = json.load(fh)

    cases = data.get("cases", [])
    total_cases = len(cases)
    all_failures: list[str] = []
    passed = 0

    for case in cases:
        failures = run_case(case)
        if failures:
            all_failures.extend(failures)
        else:
            passed += 1

    print(f"\nConformance results: {passed}/{total_cases} cases passed")

    if all_failures:
        print(f"\n{len(all_failures)} failure(s):\n")
        for failure in all_failures:
            print(f"  FAIL: {failure}")
        return 1

    print("All conformance checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
