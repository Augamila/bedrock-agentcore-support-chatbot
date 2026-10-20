# Bedrock AgentCore Support Chatbot — built and validated like a model

A customer-support chatbot for a fictional online shop, built on the **Amazon Bedrock AgentCore managed harness** (Amazon Nova Pro), with a bug-report tool exposed through an **AgentCore Gateway** (Lambda + DynamoDB). It routes every message into one of three behaviors: file a bug report (collecting description, steps to reproduce and environment over several turns), answer a platform question strictly from an FAQ, or hand off to a human line with a fixed message.

The build was a Udacity project, but the interesting part is how it was **validated**. This repo treats the chatbot as a model under governance: defined scope, a test suite with reference answers, LLM-as-a-judge scoring, and independent verification of what the model *claimed* against what the system *actually did*.

## Architecture

User message → AgentCore harness (system prompt + FAQ, Nova Pro, memory disabled) → AgentCore Gateway (MCP, IAM auth) → Lambda `create_bug_report` → DynamoDB. See `docs/architecture.md`.

AWS services: Bedrock AgentCore (harness, Gateway), Amazon Bedrock (Nova Pro via cross-region inference profile), Bedrock Evaluations, Lambda, DynamoDB, CloudFormation, IAM, CloudWatch, S3 (evaluation data).

## Validation and governance results

| Area | What was done | Result |
|---|---|---|
| Outcome analysis | 17 test cases with reference answers, scored by Bedrock Evaluations (`Builtin.Correctness`, Nova Pro as judge) | Run 1: 0.79. After prompt revisions: 0.912 |
| Change control | Prompt changes tracked per run; harness prompt re-read with `get_harness` to confirm what was deployed | See `OBSERVATIONS.md` |
| Ground-truth verification | Bug-report tickets checked directly in DynamoDB and against the raw event stream (`chat.py --verbose`) | Matching verified ticket recorded |
| Containment | Fixed verbatim hand-off message, FAQ-only answers, instruction not to treat customer text as instructions | Held on the injection test in the suite |

## Findings worth knowing about

1. **The model sometimes claimed it had filed a ticket without calling the tool.** On some runs Nova Pro returned a confident confirmation (including an invented ticket ID) with no tool-use event in the stream and no row in DynamoDB. This reproduced on two AWS accounts. It was only caught by checking DynamoDB and CloudWatch, not by reading the chat.
2. **The evaluation score does not measure tool execution.** The evaluation dataset is built from streamed text only, so 0.912 reflects response quality, not verified tool use. `chat.py` does detect tool-use events and prints a `[tool call]` line; `generate-eval-dataset.py` does not record them.
3. **AgentCore session memory is on by default** and contaminates "fresh session" tests. `create_harness.py` sets `memory={"disabled": {}}` at creation time (it cannot be changed afterwards).
4. **`<thinking>` text can leak** into replies from Nova Pro; the prompt discourages it but it should be stripped in code in production.
5. **Single-turn evaluation** cannot test multi-turn slot-filling, which is the core bug-report behavior.

## Status of optional components

| Component | Status |
|---|---|
| Harness, Gateway, Lambda, DynamoDB, evaluation run | Deployed and tested |
| `classify_and_validate.py` (structured-output validator) | Self-test passes locally; not wired into the deployed harness |
| `cloudformation-guardrail.yaml` (Bedrock Guardrails) | Lints clean; **not deployed or verified** |
| Knowledge Base / RAG (`cloudformation-knowledge-base.yaml`, `kb_setup.py`, `search_faq.py`) | **Design only**, unverified |
| `multi_turn_bug_report_test.py` | Written, **not yet run** against AWS |

## Known limitations of the prompt

The submitted `system_prompt.txt` is the version that was actually tested. It contains minor typos (a stray `..` and a `-Always be polite` line) that were left in so the file matches the evaluated deployment.

## Run it

See `IMPLEMENTATION_GUIDE.md` for the full walkthrough. In short: deploy `cloudformation-tool.yaml`, run `setup_gateway.py`, then `create_harness.py`, then `chat.py`. Requires boto3 1.43+, Nova Pro access, and us-east-1. Teardown: `cleanup_agentcore.py`.

Never commit `agentcore_config.json` (account-specific ARNs) or AWS credentials; both are git-ignored.
