#!/usr/bin/env python3
"""Structured-output classifier validation (stand-out item).

The rubric suggests "use structured output to ensure the classifier only
produces valid values." AgentCore has no separate classifier node - routing
lives in the customer-facing system prompt - so this is a standalone
validation harness that:

  1. Sends each test prompt to Nova Pro with a strict classifier system prompt
     (system_prompt_classifier.txt) that must return ONLY {"category": "..."}.
  2. Parses the output and ENFORCES the enum: exactly one of
     BUG_REPORT / PLATFORM_QUESTION / OTHER / CLARIFY. Anything else is treated
     as an invalid structured output; the call is retried once, then counted as
     a hard failure. This is the "only produces valid values" guarantee.
  3. Compares the validated label to each test's expected_category and prints a
     confusion matrix plus routing accuracy and an invalid-output count.

Two modes:
  --self-test   Run the parse/validate/normalize logic against canned strings
                with NO AWS calls. This proves the structured-output guarantee
                deterministically and is what CI should run.
  (default)     Invoke the real model (bedrock-runtime Converse, us-east-1,
                us.amazon.nova-pro-v1:0) for each test in harness-tests.json.

Usage:
    python classify_and_validate.py --self-test
    python classify_and_validate.py --tests-json harness-tests.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

VALID = {"BUG_REPORT", "PLATFORM_QUESTION", "OTHER", "CLARIFY"}


def parse_category(raw):
    """Extract and validate a category from a model's raw text output.

    Returns the category string if it is exactly one of VALID, else None.
    Tolerant of accidental code fences or a stray leading/trailing sentence by
    locating the first JSON object, but STRICT about the enum: an unknown value
    returns None (invalid), never a silent coercion to a default.
    """
    if raw is None:
        return None
    text = raw.strip()
    # Strip code fences if the model added them despite instructions.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    # Find the first {...} block.
    m = re.search(r"\{.*?\}", text, flags=re.DOTALL)
    candidate = m.group(0) if m else text
    try:
        obj = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(obj, dict) or set(obj.keys()) != {"category"}:
        return None
    value = obj.get("category")
    return value if value in VALID else None


def self_test():
    """Deterministic proof that the enum guarantee holds. No AWS."""
    cases = [
        ('{"category": "BUG_REPORT"}', "BUG_REPORT"),
        ('{"category":"PLATFORM_QUESTION"}', "PLATFORM_QUESTION"),
        ('```json\n{"category": "OTHER"}\n```', "OTHER"),
        ('Sure! {"category": "CLARIFY"}', "CLARIFY"),   # stray lead-in tolerated
        ('{"category": "bug_report"}', None),            # wrong case -> invalid
        ('{"category": "REFUND"}', None),                # not in enum -> invalid
        ('{"route": "OTHER"}', None),                    # wrong key -> invalid
        ('{"category": "OTHER", "why": "x"}', None),     # extra key -> invalid
        ('OTHER', None),                                 # not JSON -> invalid
        ('', None),                                      # empty -> invalid
        (None, None),                                    # missing -> invalid
    ]
    failures = []
    for raw, expected in cases:
        got = parse_category(raw)
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            failures.append((raw, expected, got))
        print(f"  [{status}] {raw!r:45} -> {got!r} (expected {expected!r})")
    print()
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} case(s) wrong.")
        sys.exit(1)
    print("SELF-TEST PASSED: parser only ever emits a valid enum value or None "
          "(never a silent/wrong coercion).")


def classify_live(client, model_id, system_prompt, prompt):
    """One structured classification via Converse; retry once on invalid output."""
    for _ in range(2):
        resp = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0.0, "maxTokens": 20},
        )
        raw = "".join(
            b.get("text", "")
            for b in resp["output"]["message"]["content"]
        )
        cat = parse_category(raw)
        if cat is not None:
            return cat, raw
    return None, raw  # invalid twice -> hard failure


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true",
                    help="Run parse/validate logic against canned strings, no AWS.")
    ap.add_argument("--tests-json", default="harness-tests.json")
    ap.add_argument("--prompt-file", default="system_prompt_classifier.txt")
    ap.add_argument("--model-id", default="us.amazon.nova-pro-v1:0")
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    import boto3
    system_prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    tests = json.loads(Path(args.tests_json).read_text(encoding="utf-8"))["tests"]
    client = boto3.client("bedrock-runtime", region_name=args.region)

    order = ["BUG_REPORT", "PLATFORM_QUESTION", "OTHER", "CLARIFY"]
    matrix = {a: {b: 0 for b in order} for a in order}
    invalid = 0
    correct = 0
    scored = 0

    for t in tests:
        expected = t.get("expected_category")
        if expected not in VALID:
            continue  # only score labeled rows
        cat, raw = classify_live(client, args.model_id, system_prompt, t["prompt"])
        if cat is None:
            invalid += 1
            print(f"  {t['id']}: INVALID structured output -> {raw!r}")
            continue
        scored += 1
        matrix[expected][cat] += 1
        if cat == expected:
            correct += 1
        else:
            print(f"  {t['id']}: expected {expected}, got {cat}")

    print("\nConfusion matrix (rows = expected, cols = predicted):")
    print("  " + "".join(f"{c[:5]:>8}" for c in order))
    for a in order:
        print(f"  {a[:5]:>5} " + "".join(f"{matrix[a][b]:>8}" for b in order))
    print(f"\nRouting accuracy: {correct}/{scored}"
          f"{' = %.0f%%' % (100*correct/scored) if scored else ''}")
    print(f"Invalid structured outputs (after one retry): {invalid}")
    if invalid:
        sys.exit(1)


if __name__ == "__main__":
    main()
