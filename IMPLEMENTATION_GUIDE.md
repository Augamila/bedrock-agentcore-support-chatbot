# Implementation & Submission Guide

A complete, from-zero walkthrough: set up your machine, stand up every AWS resource, build and
test the chatbot, run the Bedrock Evaluation, and collect exactly the evidence the rubric asks
for. Every command runs from the repo root (the equivalent of `project/starter/`) in **us-east-1**.

Nothing in this repo has been run against AWS for you — this guide is the exact sequence to
produce the runtime evidence (transcript, DynamoDB item, JSONL, evaluation screenshots) that only
you can generate from your own account.

---

## 0. How the rubric maps to this AgentCore project (read first)

The rubric's first criterion ("Build a Bedrock **Flow** … Condition node expressions … separate
Output node") is leftover from the older Bedrock **Flows** version of this course. This project is
the **AgentCore managed harness** version — there is no flow, no condition node, and no separate
classifier resource; all routing lives in `system_prompt.txt`. Criteria 2–4 already reflect that.
Map the evidence like this:

| Rubric asks for (Flows wording) | What you actually submit (AgentCore) |
|---|---|
| Screenshot of the full flow diagram | `docs/architecture.md` diagram (user → harness → 3 routes → gateway/Lambda/DynamoDB) |
| Screenshot of the classifier prompt configuration | The **STEP 1** routing section of `system_prompt.txt` (the classifier is in the prompt) |
| Screenshot of the Condition node expressions | The routing-conditions table in `docs/architecture.md` (state explicitly: AgentCore has no condition nodes) |
| Screenshot of the FAQ Prompt node template | `system_prompt.txt` showing the `{{FAQ}}` placeholder (and the substituted FAQ) |
| `flow-tests.json` | `harness-tests.json` (rename a copy if a grader literally wants that filename) |

If your grader strictly requires the literal Flows artifacts, that's a course-version mismatch worth
raising — but everything the AgentCore project can produce is covered above.

---

## 1. Prerequisites (local machine)

You need: an AWS account with Bedrock access, the AWS CLI, and Python 3.9+.

1. **Install the AWS CLI v2** — follow AWS's installer for your OS, then verify:
   ```bash
   aws --version
   ```
2. **Install Python 3.9+** and confirm:
   ```bash
   python3 --version
   ```
3. **Get the code** onto your machine (clone your repo or unzip this package), then `cd` into it:
   ```bash
   cd online-shop-support
   ```

---

## 2. AWS account setup (console navigation)

### 2a. Configure credentials
If you use long-lived keys:
```bash
aws configure         # enter Access Key, Secret Key, region us-east-1, output json
```
If your lab gives temporary credentials, export all three together (exporting only the session
token does nothing):
```bash
export AWS_ACCESS_KEY_ID="ASIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."
export AWS_DEFAULT_REGION=us-east-1
```
Verify you're authenticated:
```bash
aws sts get-caller-identity
```

### 2b. Enable the Amazon Nova Pro model (required)
The project pins `us.amazon.nova-pro-v1:0`. You must enable it or every model call fails with
`AccessDenied … InvokeModel`.

1. AWS Console → search **Bedrock** → open **Amazon Bedrock**.
2. Confirm the region (top-right) is **US East (N. Virginia) us-east-1**.
3. Left sidebar → **Model access** (under *Bedrock configurations*).
4. Click **Enable specific models** (or **Manage model access**).
5. Check **Amazon → Nova Pro**, click **Next**, then **Submit**. Access is usually granted in
   seconds; the status turns to **Access granted**.

> For a cross-region inference profile (the `us.` prefix), your harness role also needs
> `bedrock:InvokeModel` on both the profile and the underlying foundation model — the provided
> `cloudformation-tool.yaml` already grants this broadly.

---

## 3. Python environment

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -c "import boto3; print(boto3.__version__)"   # must be 1.43.76+
```

If you see `AttributeError: … has no attribute 'list_harnesses'`, your boto3 is < 1.43 — upgrade:
```bash
pip install --upgrade "boto3>=1.43.0" "botocore>=1.43.0"
```
If an old `awscli` v1 pins `botocore==1.42.54` and blocks the upgrade, remove it from the venv (the
system AWS CLI still works): `pip uninstall -y awscli`, then upgrade boto3 again.

---

## 4. Step 1 — Deploy the tool stack (DynamoDB + Lambda + IAM roles)

```bash
aws cloudformation deploy \
  --template-file cloudformation-tool.yaml \
  --stack-name bug-report-tool-stack \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1
```
`CAPABILITY_NAMED_IAM` is required because the template creates named roles. This creates:

| Resource | Name |
|---|---|
| DynamoDB table | `bug-report-tool-stack-bug-reports` |
| Lambda | `bug-report-tool-stack-create-bug-report` |
| Lambda role / gateway role / harness role | `bug-report-tool-stack-{lambda,gateway,harness}-role` |

### 4a. Verify the Lambda in isolation (console)
1. Console → **Lambda** → **Functions** → `bug-report-tool-stack-create-bug-report`.
2. **Test** tab → **Create new event** → name it `t1` → paste:
   ```json
   {
     "description": "The checkout page crashes when I click the Pay button",
     "stepsToReproduce": "1. Add an item to the cart. 2. Go to checkout. 3. Click Pay.",
     "environment": "Chrome 120 on macOS Sonoma"
   }
   ```
3. Click **Test**. Expect a response with a `ticketId` and `"status": "OPEN"`.
4. Confirm the row landed (console): **DynamoDB** → **Tables** → `bug-report-tool-stack-bug-reports`
   → **Explore table items** → **Run**. Or via CLI:
   ```bash
   aws dynamodb scan --table-name bug-report-tool-stack-bug-reports --region us-east-1
   ```
   (This first item is your Lambda smoke-test, not the chatbot's — that comes in Step 8.)

Troubleshooting: `AccessDeniedException` → the DynamoDB policy is on the wrong role;
`ResourceNotFoundException` → the Lambda's `TABLE_NAME` env var doesn't match the table. The Lambda
prints every event to CloudWatch Logs (`/aws/lambda/bug-report-tool-stack-create-bug-report`) — the
ground truth for what reached it.

---

## 5. Step 2 — Create the gateway

```bash
python setup_gateway.py
```
This reads the stack outputs, creates an AgentCore Gateway, registers the Lambda under target
`bugreports` (so the model sees `bugreports___create_bug_report`), and writes
`agentcore_config.json`. If it fails right after the stack finishes with a role error, that's IAM
propagation delay — wait a minute and re-run.

---

## 6. (Optional, stand-out) Step 2.5 — Deploy the Bedrock Guardrail

Adds an independent content/prompt-attack filter in front of the model — see
`cloudformation-guardrail.yaml`.
```bash
aws cloudformation deploy \
  --template-file cloudformation-guardrail.yaml \
  --stack-name bug-report-guardrail-stack \
  --region us-east-1
aws cloudformation describe-stacks --stack-name bug-report-guardrail-stack \
  --query 'Stacks[0].Outputs' --output table --region us-east-1
```
Note the `GuardrailId` and `GuardrailVersion`; pass them to `create_harness.py` in the next step.

---

## 7. Step 3 — Create the harness

`system_prompt.txt` is already written (keep the `{{FAQ}}` placeholder — `create_harness.py`
substitutes `online_shop_faq.md` automatically). Create the harness:
```bash
python create_harness.py
# with the optional guardrail:
python create_harness.py --guardrail-id <GuardrailId> --guardrail-version <GuardrailVersion>
```
First run takes ~2–3 minutes. Re-run this after every prompt edit (it updates the existing harness).

> **Memory is disabled at creation** (`memory={"disabled": {}}` in `create_harness.py`). This is a
> disclosed change to the starter: AgentCore memory defaults on and leaks across sessions, which
> contaminates the fresh-session eval cases and drifts scores between runs. To verify:
> ```bash
> python -c "import json,boto3;cfg=json.load(open('agentcore_config.json'));c=boto3.client('bedrock-agentcore-control',region_name=cfg['region']);print(c.get_harness(harnessId=cfg['harness_id'])['harness'].get('memory'))"
> ```
> Expect `{'disabled': {}}`. Changing memory on an existing harness isn't enough — rebuild with
> `cleanup_agentcore.py` → `setup_gateway.py` → `create_harness.py` if it's ever on.

---

## 8. Step 4 — Manual chat test and capture the transcript

```bash
python chat.py
```
Run these messages (one conversation each is fine) and confirm the behavior, capturing a transcript
for the bug-report case:

| Send | Expect |
|---|---|
| `On Safari 17 on my iPhone, product images never load on category pages. I open the shop, tap any category, and every image is a grey box.` | Files the ticket **immediately**, prints `[tool call] bugreports___create_bug_report`, relays a ticket ID. |
| `Your checkout page crashes every time I click the Pay button.` | Asks for the **browser/device**; does **not** file. |
| `How long do I have to send an item back?` | FAQ answer with the real figure (30 days). |
| `Do you offer a student discount?` | Not in FAQ — hands off to **1-800-555-0199**. |
| `I'd like to speak to a real person.` | Polite redirect to **1-800-555-0199**, no tool call. |

Save a bug-report transcript that shows the follow-up questions **and** the
`[tool call] bugreports___create_bug_report` line — this is required evidence:
```bash
python chat.py | tee bug-report-transcript.txt
```
Then capture the persisted ticket for evidence:
```bash
aws dynamodb scan --table-name bug-report-tool-stack-bug-reports --region us-east-1 \
  --query 'Items[].[ticketId.S,status.S]' --output table | tee dynamodb-tickets.txt
```
Also take a **DynamoDB console screenshot**: DynamoDB → Tables → `bug-report-tool-stack-bug-reports`
→ **Explore table items** → showing at least one item the chatbot created.

### (Optional, stand-out) Multi-turn DynamoDB verification
```bash
python multi_turn_bug_report_test.py
```
Drives a 5-turn bug report in one session and asserts the DynamoDB item matches what the customer
said. Exits non-zero on any mismatch. Great supplementary evidence that stateful collection works.

---

## 9. Step 5 — Generate the evaluation dataset

```bash
python generate-eval-dataset.py --tests-json harness-tests.json
head -1 output_eval_dataset.jsonl
```
This invokes the harness once per test in a fresh session and writes `output_eval_dataset.jsonl`
with `modelIdentifier: my-support-chatbot`. Any `[HARNESS_ERROR]` in a response means that
invocation failed — fix it before evaluating (usually model access or a missing harness ARN; run
`create_harness.py` first if the latter).

---

## 10. Step 6 — Deploy the testing stack (S3 + eval role)

```bash
aws cloudformation deploy \
  --template-file cloudformation-testing.yaml \
  --stack-name bug-report-testing-stack \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1

aws cloudformation describe-stacks --stack-name bug-report-testing-stack \
  --query 'Stacks[0].Outputs' --output table --region us-east-1
```
Record `EvalDatasetBucketName` and `BedrockEvalRoleArn`.

---

## 11. Step 7 — Run the Bedrock Evaluation (LLM-as-a-judge, BYOI)

Upload the dataset:
```bash
aws s3 cp output_eval_dataset.jsonl \
  s3://<EvalDatasetBucketName>/output_eval_dataset.jsonl --region us-east-1
```
Create the job. Inline JSON often breaks in shells, so the robust way is to write the three configs
to files first:
```bash
python -c "
import json
json.dump({'automated':{'datasetMetricConfigs':[{'taskType':'General','dataset':
 {'name':'support-chatbot-eval-dataset','datasetLocation':
 {'s3Uri':'s3://<EvalDatasetBucketName>/output_eval_dataset.jsonl'}},
 'metricNames':['Builtin.Correctness']}],
 'evaluatorModelConfig':{'bedrockEvaluatorModels':[{'modelIdentifier':'amazon.nova-pro-v1:0'}]}}},
 open('eval-config.json','w'))
json.dump({'models':[{'precomputedInferenceSource':{'inferenceSourceIdentifier':'my-support-chatbot'}}]},
 open('inference-config.json','w'))
json.dump({'s3Uri':'s3://<EvalDatasetBucketName>/results/'}, open('output-config.json','w'))
"
aws bedrock create-evaluation-job \
  --job-name support-chatbot-eval-run-1 \
  --role-arn <BedrockEvalRoleArn> \
  --evaluation-config file://eval-config.json \
  --inference-config file://inference-config.json \
  --output-data-config file://output-config.json \
  --region us-east-1
```
`inferenceSourceIdentifier` **must** equal the `modelIdentifier` in the JSONL (`my-support-chatbot`)
or the job matches zero records.

Watch status:
```bash
aws bedrock list-evaluation-jobs --region us-east-1 \
  --query 'jobSummaries[].[jobName,status]' --output table
```

### View and screenshot the results (console)
1. Console → **Amazon Bedrock** → **Evaluations** → click `support-chatbot-eval-run-1` once it's
   **Completed**.
2. Screenshot the **Metrics summary** and the **score distribution**.
3. Open the **per-record / per-prompt table** and screenshot it — read this, not just the average.
   A 0.80 can be ten mediocre answers or eight good and two failures.

### If the score is low, iterate
Edit `system_prompt.txt` → `python create_harness.py` → `python generate-eval-dataset.py …` → new
job with a **new name and output prefix**. Record what changed and why in `OBSERVATIONS.md`. The
usual culprits (already handled in this prompt, but verify): non-standardized hand-off wording,
single-turn bug cases judged as if they should file immediately, and the late-package/declined-card
cases being misrouted as bugs.

---

## 12. Step 8 — Collect evidence, then fill in the writeups

```bash
aws s3 sync s3://<EvalDatasetBucketName>/results/ ./eval-results-backup/ --region us-east-1
```
Now fill in `OBSERVATIONS.md` with your **real** numbers and per-prompt findings (it has the
structure and placeholders ready).

**Submission bundle** (maps to the rubric):

| Rubric criterion | Artifact(s) |
|---|---|
| Classification & routing | `system_prompt.txt` (STEP 1) + `docs/architecture.md` (diagram, classifier reference, routing table) + evaluation results showing each route scored |
| Bug report path | `system_prompt.txt` (bug route) + `bug-report-transcript.txt` (follow-ups + `[tool call] bugreports___create_bug_report`) + DynamoDB screenshot/`dynamodb-tickets.txt` |
| Platform + Other paths | `system_prompt.txt` with `{{FAQ}}` + eval results for a covered question, an uncovered question, and an other-request (t6/t9/t10 cover these) |
| Testing & evaluation | `harness-tests.json` + `output_eval_dataset.jsonl` + evaluation results screenshots + `OBSERVATIONS.md` |

Two things that trigger a resubmission: (1) the transcript and the diagram must describe the **same
system**; (2) **disclose** the memory-line edit to `create_harness.py` — it's already disclosed in
`README.md`, `PROJECT_NOTES.md`, and this guide, so keep those in the bundle.

---

## 13. Cleanup (avoid ongoing charges)

Empty the eval bucket **before** deleting the testing stack, or that stack ends in `DELETE_FAILED`.
```bash
python cleanup_agentcore.py
aws s3 rm s3://<EvalDatasetBucketName> --recursive --region us-east-1
aws cloudformation delete-stack --stack-name bug-report-testing-stack --region us-east-1
aws cloudformation delete-stack --stack-name bug-report-tool-stack --region us-east-1
# if you deployed the optional guardrail:
aws cloudformation delete-stack --stack-name bug-report-guardrail-stack --region us-east-1
deactivate ; rm -rf venv
```

---

## 14. Quick troubleshooting reference

| Symptom | Fix |
|---|---|
| `InvalidClientTokenId` / `NoCredentialsError` | Re-export all three credential values; check `aws sts get-caller-identity`. |
| `has no attribute 'list_harnesses'` | Upgrade to `boto3`/`botocore` 1.43.76+. |
| `AccessDenied … InvokeModel` | Enable Nova Pro in Bedrock → Model access (Step 2b). |
| `403 Forbidden` on the gateway `/mcp` URL | Harness role needs `bedrock-agentcore:InvokeGateway` (provided template grants it). |
| `Model produced invalid sequence as part of ToolUse` | Temperature 0 (set), and keep only `type`/`properties`/`required` at the top level of the tool schema (the provided schema already does). |
| `<thinking>` text in replies/JSONL | Prompt already forbids it; if it recurs, re-run `create_harness.py` after confirming the prompt's "end every turn with only a customer-facing message" rule. |
| Scores differ between identical runs | Harness memory is on — verify it's disabled (Step 7) and rebuild. |
| Eval job matches no records | `inferenceSourceIdentifier` must equal `modelIdentifier` (`my-support-chatbot`). |
| Testing stack `DELETE_FAILED` | Bucket not empty — `aws s3 rm … --recursive`, then delete the stack again. |
