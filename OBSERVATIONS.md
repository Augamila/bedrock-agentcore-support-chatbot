# Evaluation Observations

The runbook asks for written observations on the Bedrock Evaluations results - specifically
what you changed between runs and why, based on the per-prompt table (not just the average
score). This file is structured for that, with the design rationale pre-filled and clearly
marked placeholders for the numbers you can only get by running the job against real AWS.

> IMPORTANT: The `<...>` placeholders below must be filled in from your actual evaluation
> runs. Nothing in this project has been executed against AWS in preparing it, so no real
> scores exist yet. Read the per-prompt table in the Bedrock console (Evaluations -> your
> job) and record the true numbers.

## Run 1 (baseline)

- Job name: `support-chatbot-eval-run-1`
- Metric: `Builtin.Correctness`, evaluator `amazon.nova-pro-v1:0`
- Overall score: `<fill in>`
- Per-route summary:
  - Bug report cases (t1-t3, t15): `<fill in>`
  - Platform/FAQ cases (t4-t8, t15): `<fill in>`
  - Hand-off/OTHER cases (t9-t12, t16): `<fill in>`
  - Edge/injection cases (t13, t14, t16, t17): `<fill in>`
- Notable per-prompt results (which specific prompts scored low, and the actual model output
  that caused it): `<fill in - quote the response and say why the judge marked it down>`

## What the design already accounts for (why these cases exist)

These are the failure modes the runbook and the Common Configuration Issues guide call out,
and how the prompt/test suite is built to avoid them. If a run scores low, check these first:

1. **Refusal-wording variance (Config-Issues Q6).** The evaluator expects a *standardized*
   transfer message. The prompt gives one exact hand-off sentence (to 1-800-555-0199) and
   tells the model to use it verbatim; every OTHER/hand-off test expects that same wording.
   If hand-off cases score low, confirm the model is using the line verbatim rather than
   paraphrasing.
2. **Single-turn vs multi-turn expectation mismatch (Config-Issues Q6).** Each eval case runs
   in a fresh session, so a bug prompt missing fields must be judged on *starting collection*,
   not on a finished ticket. Only t1 (all three fields present) expects a ticket ID; t2/t3
   expect a single follow-up question. If bug cases score low, check that the reference
   `expected` text matches this, and that the prompt asks one field at a time.
3. **Ambiguous-category anchors (Runbook Step 7).** Late/missing package = FAQ (t4), declined
   card = FAQ (t5), checkout error = bug (t2). These are the boundary cases most likely to be
   misrouted; they're tested explicitly. A low score here usually means the category
   definitions need to be even more explicit about the anchor.
4. **Memory contamination (Runbook Step 8).** AgentCore memory defaults ON and leaks across
   sessions, which makes scores drift between identical runs. `create_harness.py` disables it
   at create time (`memory={"disabled": {}}`). If two identical runs disagree, verify memory
   is actually disabled (the runbook's one-liner get_harness check) and rebuild if needed.
5. **`<thinking>` leakage (Runbook troubleshooting).** Nova can emit chain-of-thought into the
   reply and the JSONL, which the judge penalizes. The prompt requires every turn to end with
   only a customer-facing message and no reasoning text. If responses contain stray planning
   text, tighten that rule.

## Run 2 (after iterating) - if needed

- What changed and why (tie each change to a specific low-scoring prompt from Run 1):
  `<fill in>`
- Job name / new output prefix: `<fill in>`
- Overall score: `<fill in>` (before -> after: `<fill in>`)
- Did the targeted prompts improve? `<fill in>`

> A before/after with a stated reason is stronger evidence than a single score. If you only
> run once, say so and keep the Run 1 analysis thorough.
