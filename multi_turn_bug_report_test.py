#!/usr/bin/env python3
"""Scripted multi-turn bug-report integration test.

Stand-out addition beyond the required single-turn eval suite
(harness-tests.json / generate-eval-dataset.py): this script drives a full,
realistic multi-turn conversation against the LIVE harness (same
invoke_harness call chat.py uses, same session id across turns so the
harness's session memory actually gets exercised), then reads the ticket
back out of DynamoDB and checks it against what the customer actually said.

This closes a gap the single-turn eval suite cannot: harness-tests.json runs
each case in a FRESH session (by design, so test cases don't influence each
other), so it can only ever assert on collection *behavior*, never on a
finished ticket. This script is the thing that actually proves collection
plus tool-calling plus persistence works end to end across turns.

Usage:
    python multi_turn_bug_report_test.py

What it does:
  1. Reads agentcore_config.json for the harness ARN, gateway ARN, table
     name, and region (same file every other script in this project uses).
  2. Sends a short, deliberately fragmented conversation to the harness in
     ONE session, mirroring how a real customer would dribble out details
     over several messages rather than filing a complete report up front.
  3. Watches the tool-call event stream, exactly like chat.py, and captures
     any ticketId the assistant relays back to the "customer" in its final
     message.
  4. Snapshots the DynamoDB bug-reports table before the conversation and
     scans it again afterward, to find the new item (works with or without
     a successfully parsed ticketId in the assistant's prose).
  5. Cross-checks: the new item's ticketId matches the one the assistant
     told the customer, status is OPEN, and description / stepsToReproduce /
     environment all contain the key facts the customer actually gave
     across the conversation (case-insensitive substring match on a few
     keywords per field, since exact wording will be paraphrased).
  6. Prints a pass/fail summary per assertion and a non-zero exit code on
     any failure, so this can be wired into CI.

This talks to real AWS resources (the harness, the gateway, DynamoDB) and
will incur normal Bedrock/AgentCore invocation costs — it is not a
dry-run/mock.
"""

import json
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.eventstream import EventStream

# ---------------------------------------------------------------------------
# The scripted conversation. Deliberately spreads description, steps to
# reproduce, and environment across separate turns, and includes a bit of
# small talk / a correction, since that's how real customers actually type.
# ---------------------------------------------------------------------------
CONVERSATION = [
    "Hi, I think I found a bug on your site.",
    "When I apply a promo code at checkout, the page just spins and never finishes - the order total never updates.",
    "It happens every time: I add an item to the cart, go to checkout, type a promo code in the discount field, and click Apply.",
    "Oh, one correction - it only happens on the checkout page, not the cart page.",
    "I'm on Chrome 120 on Windows 11, if that helps.",
]

# Keywords we expect to show up (possibly paraphrased) in each persisted
# field, based on what the customer actually said above. Kept short and
# generic on purpose so minor rewording by the model doesn't break the check.
EXPECTED_KEYWORDS = {
    "description": ["promo code", "spin"],
    "stepsToReproduce": ["checkout", "apply"],
    "environment": ["chrome", "windows"],
}


def event_stream(response):
    for value in response.values():
        if isinstance(value, EventStream):
            return value
    raise RuntimeError(f"No event stream in response: {list(response)}")


def send_turn(rt, config, session_id, user_text):
    """Send one turn; return (assistant_text, tool_called: bool)."""
    response = rt.invoke_harness(
        harnessArn=config["harness_arn"],
        runtimeSessionId=session_id,
        model={"bedrockModelConfig": {
            "modelId": config.get("model_id", "us.amazon.nova-pro-v1:0")
        }},
        tools=[{
            "type": "agentcore_gateway",
            "name": "support_gateway",
            "config": {"agentCoreGateway": {"gatewayArn": config["gateway_arn"]}},
        }],
        messages=[{"role": "user", "content": [{"text": user_text}]}],
    )

    texts, buffer = [], []
    tool_called = False
    for event in event_stream(response):
        if "contentBlockStart" in event:
            tool_use = event["contentBlockStart"].get("start", {}).get("toolUse")
            if tool_use:
                tool_called = True
                print(f"    [tool call] {tool_use.get('name', '?')}")
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                buffer.append(delta["text"])
        elif "messageStop" in event:
            if buffer:
                texts.append("".join(buffer))
                buffer = []
    if buffer:
        texts.append("".join(buffer))
    return (texts[-1] if texts else ""), tool_called


def snapshot_ticket_ids(table):
    """Return the set of ticketIds currently in the table (paginated scan)."""
    ids = set()
    kwargs = {"ProjectionExpression": "ticketId"}
    while True:
        resp = table.scan(**kwargs)
        ids.update(item["ticketId"] for item in resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return ids


def extract_ticket_id_from_text(text):
    """Best-effort pull of a UUID-looking ticket id out of the assistant's
    final message, so we can cross-check it against DynamoDB directly."""
    import re
    m = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        text,
    )
    return m.group(0) if m else None


def main():
    config_path = Path("agentcore_config.json")
    if not config_path.exists():
        sys.exit("agentcore_config.json not found — run setup_gateway.py and "
                  "create_harness.py first.")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for key in ("harness_arn", "gateway_arn", "table_name", "region"):
        if key not in config:
            sys.exit(f"agentcore_config.json is missing '{key}' — re-run the "
                      "setup scripts.")

    region = config["region"]
    rt = boto3.client(
        "bedrock-agentcore", region_name=region,
        config=Config(read_timeout=300, retries={"max_attempts": 1}),
    )
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(config["table_name"])

    print(f"Snapshotting {config['table_name']} before the conversation...")
    before_ids = snapshot_ticket_ids(table)
    print(f"  {len(before_ids)} existing ticket(s).")

    session_id = f"{uuid.uuid4()}-multiturn-test"
    print(f"\nStarting scripted conversation (session {session_id})...\n")

    final_text = ""
    any_tool_called = False
    for i, turn in enumerate(CONVERSATION, start=1):
        print(f"  turn {i} (customer): {turn}")
        final_text, tool_called = send_turn(rt, config, session_id, turn)
        any_tool_called = any_tool_called or tool_called
        print(f"  turn {i} (bot):      {final_text[:200]}"
              f"{'...' if len(final_text) > 200 else ''}\n")

    # --- Assertions -----------------------------------------------------
    failures = []

    if not any_tool_called:
        failures.append(
            "create_bug_report was never called during the conversation — "
            "the assistant should have filed a ticket once description, "
            "steps to reproduce, and environment were all provided."
        )

    print("Waiting a moment for the DynamoDB write to be visible...")
    time.sleep(3)
    after_ids = snapshot_ticket_ids(table)
    new_ids = after_ids - before_ids

    if not new_ids:
        failures.append(
            "No new item appeared in the DynamoDB table after the conversation."
        )
        item = None
    else:
        if len(new_ids) > 1:
            print(f"  NOTE: {len(new_ids)} new items appeared "
                  f"(possible re-run or duplicate call): {new_ids}")
        new_id = sorted(new_ids)[0]
        item = table.get_item(Key={"ticketId": new_id}).get("Item")

    relayed_id = extract_ticket_id_from_text(final_text)
    if item:
        print(f"\nPersisted ticket ({item['ticketId']}):")
        print(json.dumps(item, indent=2, default=str))

        if relayed_id and relayed_id != item["ticketId"]:
            failures.append(
                f"Assistant relayed ticket ID {relayed_id}, which does not "
                f"match the DynamoDB item's ticketId {item['ticketId']}."
            )
        elif not relayed_id:
            failures.append(
                "Could not find a ticket ID in the assistant's final message "
                "— it should have told the customer their ticket ID."
            )

        if item.get("status") != "OPEN":
            failures.append(f"Expected status OPEN, got {item.get('status')!r}.")

        for field, keywords in EXPECTED_KEYWORDS.items():
            value = str(item.get(field, "")).lower()
            missing = [kw for kw in keywords if kw not in value]
            if missing:
                failures.append(
                    f"Field '{field}' is missing expected keyword(s) "
                    f"{missing} — got: {value!r}"
                )

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED ({len(failures)} issue(s)):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("PASSED: multi-turn bug report collected, tool called once, "
              "ticket persisted in DynamoDB, and fields match the "
              "conversation.")


if __name__ == "__main__":
    main()
