#!/usr/bin/env python3
"""DESIGN-ONLY / UNVERIFIED: create the Bedrock Knowledge Base after the
cloudformation-knowledge-base.yaml stack exists.

CloudFormation cannot create an OpenSearch Serverless vector INDEX, and
AWS::Bedrock::KnowledgeBase requires the index to already exist. So this script
does the ordered steps CFN can't:

  1. Create the vector index in the collection (opensearch-py, SigV4 auth).
  2. Upload online_shop_faq.md to the KB source S3 bucket.
  3. Create the Bedrock Knowledge Base (OpenSearch Serverless storage, Titan v2
     embeddings) and an S3 data source.
  4. Start an ingestion job so the FAQ is chunked, embedded, and indexed.
  5. Save knowledge_base_id / data_source_id to agentcore_config.json.

This has NOT been run against AWS. It follows AWS's documented pattern, but the
exact index mapping, the propagation delay between creating the data-access
policy and creating the index, and the KB storage-configuration field names are
the kinds of things that need one real run to confirm. Treat it as a starting
point, not turnkey code.

Extra dependencies (not in requirements.txt):  pip install opensearch-py requests-aws4auth

Usage:
    python kb_setup.py \
      --collection-arn <CollectionArn> \
      --collection-endpoint <CollectionEndpoint> \
      --bucket <FaqSourceBucketName> \
      --kb-role-arn <KnowledgeBaseRoleArn>
"""

import argparse
import json
import time
from pathlib import Path

import boto3

REGION = "us-east-1"
EMBED_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
INDEX_NAME = "shop-faq-index"
VECTOR_FIELD = "faq-vector"
TEXT_FIELD = "text"
METADATA_FIELD = "metadata"
EMBED_DIM = 1024  # Titan Text Embeddings V2 default dimension


def create_index(collection_endpoint):
    """Create the k-NN vector index the KB will write into."""
    from opensearchpy import OpenSearch, RequestsHttpConnection
    from requests_aws4auth import AWS4Auth

    creds = boto3.Session().get_credentials()
    auth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "aoss",
                    session_token=creds.token)
    host = collection_endpoint.replace("https://", "")
    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True, verify_certs=True,
        connection_class=RequestsHttpConnection,
    )
    body = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                VECTOR_FIELD: {
                    "type": "knn_vector",
                    "dimension": EMBED_DIM,
                    "method": {"name": "hnsw", "engine": "faiss",
                               "space_type": "l2"},
                },
                TEXT_FIELD: {"type": "text"},
                METADATA_FIELD: {"type": "text"},
            }
        },
    }
    if client.indices.exists(INDEX_NAME):
        print(f"  index {INDEX_NAME} already exists")
    else:
        client.indices.create(INDEX_NAME, body=body)
        print(f"  created index {INDEX_NAME}; waiting for it to settle...")
        time.sleep(30)  # AOSS index creation is eventually consistent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection-arn", required=True)
    ap.add_argument("--collection-endpoint", required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--kb-role-arn", required=True)
    ap.add_argument("--faq-file", default="online_shop_faq.md")
    ap.add_argument("--config", default="agentcore_config.json")
    ap.add_argument("--kb-name", default="shop-faq-kb")
    args = ap.parse_args()

    print("1. Creating the vector index...")
    create_index(args.collection_endpoint)

    print("2. Uploading FAQ to the source bucket...")
    boto3.client("s3", region_name=REGION).upload_file(
        args.faq_file, args.bucket, "online_shop_faq.md")

    agent = boto3.client("bedrock-agent", region_name=REGION)

    print("3. Creating the Knowledge Base...")
    kb = agent.create_knowledge_base(
        name=args.kb_name,
        roleArn=args.kb_role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": EMBED_MODEL_ARN
            },
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": args.collection_arn,
                "vectorIndexName": INDEX_NAME,
                "fieldMapping": {
                    "vectorField": VECTOR_FIELD,
                    "textField": TEXT_FIELD,
                    "metadataField": METADATA_FIELD,
                },
            },
        },
    )
    kb_id = kb["knowledgeBase"]["knowledgeBaseId"]
    print(f"  knowledgeBaseId: {kb_id}")

    print("4. Creating the S3 data source...")
    ds = agent.create_data_source(
        knowledgeBaseId=kb_id,
        name="shop-faq-s3",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {"bucketArn": f"arn:aws:s3:::{args.bucket}"},
        },
    )
    ds_id = ds["dataSource"]["dataSourceId"]

    print("5. Starting the ingestion job...")
    agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)

    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    cfg["knowledge_base_id"] = kb_id
    cfg["knowledge_base_data_source_id"] = ds_id
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"\nDone. Saved knowledge_base_id to {args.config}.")
    print("Ingestion runs asynchronously; check status in the Bedrock console "
          "(Knowledge Bases -> your KB -> Data source -> Sync history).")


if __name__ == "__main__":
    main()
