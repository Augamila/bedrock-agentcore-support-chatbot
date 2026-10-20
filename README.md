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

## Evidence

Account identifiers and local paths are redacted with solid boxes.

**A tool call, matched to its database record.** The chat shows the `[tool call] bugreports___create_bug_report` line and the ticket ID the tool returned; the DynamoDB item has the same ticket ID with the three collected fields and status `OPEN`.

![Chat session with the tool call and ticket ID, followed by a DynamoDB get-item for the same ID](evidence/ticket-chat-full-session.png)
![Tail of the same session and the full get-item result](evidence/ticket-chat-and-get-item.png)
![The same item in the DynamoDB console](evidence/ticket-dynamodb-record.png)

**Routing, FAQ grounding and hand-off.** This transcript is from an earlier prompt revision, before the fixes described in `OBSERVATIONS.md`. It still contains two defects that were later addressed: a leaked `<thinking>` block, and a ticket "48392" reported with no `[tool call]` line, which is the fabrication failure described above. The FAQ answers, the fixed hand-off message and the prompt-injection refusal are visible.

![Multi-turn transcript covering bug reports, FAQ answers, hand-offs and an injection attempt](evidence/faq-handoff-injection-transcript.png)

**Evaluation runs (Bedrock Evaluations, `Builtin.Correctness`, 17 prompts).** Run 1 averaged 0.79 (2 prompts scored 0, 3 scored 0.5, 12 scored about 0.9). After prompt revisions, run 2 averaged 0.912 (1 prompt at 0, 1 at 0.5, 15 at about 0.9). The per-prompt view for run 1 shows the failure modes the revisions targeted: leaked `<thinking>` text, internal category labels shown to the customer, and unnecessary clarifying questions when the message already contained all three bug-report fields. The scores come from an LLM judge on response text only; they do not verify that tools were actually called.

![Run 1 metrics summary, correctness 0.79](evidence/eval-run1-metrics-0.79.png)
![Run 1 score distribution](evidence/eval-run1-score-distribution.png)
![Run 1 per-prompt generation output against ground truth](evidence/eval-run1-per-prompt.png)
![Run 2 score distribution, average 0.912](evidence/eval-run2-score-distribution-0.912.png)
![Evaluation jobs list](evidence/eval-jobs-list.png)

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
