"""DESIGN-ONLY / UNVERIFIED: Lambda behind a second AgentCore Gateway tool,
`search_faq`, that retrieves FAQ passages from the Bedrock Knowledge Base.

This is the retrieval half of the RAG stand-out: instead of embedding the whole
FAQ in the prompt, the model calls search_faq(query) and answers from the top
passages returned here. Register it as a second gateway target the same way
setup_gateway.py registers create_bug_report (tool name letters/digits/
underscores only), and use system_prompt_rag.txt as the harness prompt.

Env var: KNOWLEDGE_BASE_ID (set on the Lambda to the id kb_setup.py saved).

NOT run against AWS. The Retrieve call shape follows the documented API, but the
gateway->Lambda event shape for a second tool and the exact result formatting
that works best for Nova are the parts to confirm on a real run.
"""

import json
import os

import boto3

runtime = boto3.client("bedrock-agent-runtime")
KB_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
TOP_K = int(os.environ.get("TOP_K", "4"))


def lambda_handler(event, context):
    print("EVENT:", json.dumps(event, default=str))
    query = ""
    if isinstance(event, dict):
        query = str(event.get("query") or "").strip()
    if not query:
        return {"error": "missing required field: query"}
    if not KB_ID:
        return {"error": "KNOWLEDGE_BASE_ID is not configured on the Lambda"}

    resp = runtime.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {"numberOfResults": TOP_K}
        },
    )
    passages = [
        r.get("content", {}).get("text", "")
        for r in resp.get("retrievalResults", [])
    ]
    # Hand the model the raw passages; the prompt tells it to answer only from
    # these and to hand off if they don't contain the answer.
    return {"passages": passages, "count": len(passages)}
