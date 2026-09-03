#!/usr/bin/env python3
"""Run your test suite against the harness and write a Bedrock Evaluations JSONL dataset.

Usage: python generate-eval-dataset.py --tests-json harness-tests.json
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import boto3
from botocore.config import Config
from botocore.eventstream import EventStream


def _event_stream(response):
    for value in response.values():
        if isinstance(value, EventStream):
            return value
    raise RuntimeError(f"No event stream in response: {list(response)}")


def invoke_harness_once(
    rt,
    harness_arn: str,
    gateway_arn: str,
    model_id: str,
    prompt: str,
) -> Dict[str, Any]:
    tools = []
    if gateway_arn:
        tools = [{
            "type": "agentcore_gateway",
            "name": "support_gateway",
            "config": {"agentCoreGateway": {"gatewayArn": gateway_arn}},
        }]

    response = rt.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=f"{uuid.uuid4()}-evalcase",
        model={"bedrockModelConfig": {"modelId": model_id}},
        tools=tools,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )

    texts = []
    buffer = []
    for event in _event_stream(response):
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                buffer.append(delta["text"])
        elif "messageStop" in event:
            if buffer:
                texts.append("".join(buffer))
                buffer = []
    if buffer:
        texts.append("".join(buffer))

    return {"final_output_text": texts[-1] if texts else ""}


def main():
    p = argparse.ArgumentParser(
        description="Run harness tests and emit Bedrock Evaluations JSONL "
                    "(LLM-as-judge BYOI).")
    p.add_argument("--tests-json", required=True,
                   help="Path to the test suite JSON "
                        "(see harness-tests-template.json).")
    p.add_argument("--config", default="agentcore_config.json",
                   help="Config file written by the setup scripts "
                        "(provides harness/gateway ARNs).")
    p.add_argument("--harness-arn", default=None,
                   help="Harness ARN (overrides the config file).")
    p.add_argument("--gateway-arn", default=None,
                   help="Gateway ARN to attach (overrides the config file).")
    p.add_argument("--model-id", default="us.amazon.nova-pro-v1:0",
                   help="Bedrock model id to pin on every invoke.")
    p.add_argument("--model-identifier", default="my-support-chatbot",
                   help="Value to put in modelResponses[0].modelIdentifier.")
    p.add_argument("--out-jsonl", default="output_eval_dataset.jsonl",
                   help="Where to write the eval dataset JSONL.")
    p.add_argument("--region", default=None,
                   help="AWS region (default: from config file, else us-east-1).")
    args = p.parse_args()

    config = {}
    if Path(args.config).exists():
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    harness_arn = args.harness_arn or config.get("harness_arn")
    gateway_arn = args.gateway_arn or config.get("gateway_arn")
    region = args.region or config.get("region") or "us-east-1"
    if not harness_arn:
        sys.exit("No harness ARN — pass --harness-arn or run create_harness.py "
                 "first so it is recorded in the config file.")

    suite = json.loads(Path(args.tests_json).read_text(encoding="utf-8"))
    tests = suite["tests"]

    rt = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=300, retries={"max_attempts": 1}),
    )

    out_path = Path(args.out_jsonl)
    n_ok = 0

    with out_path.open("w", encoding="utf-8") as f:
        for t in tests:
            test_id = t["id"]
            reference = t.get("expected", "")
            prompt = t.get("prompt", "")

            try:
                result = invoke_harness_once(
                    rt=rt,
                    harness_arn=harness_arn,
                    gateway_arn=gateway_arn,
                    model_id=args.model_id,
                    prompt=prompt,
                )
                response_text = result["final_output_text"]
                n_ok += 1
            except Exception as e:
                print(e, file=sys.stderr)
                response_text = f"[HARNESS_ERROR] {type(e).__name__}: {e}"

            record = {
                "prompt": prompt,
                "referenceResponse": reference,
                "modelResponses": [
                    {
                        "response": response_text,
                        "modelIdentifier": args.model_identifier,
                    }
                ],
            }

            f.write(json.dumps(record, ensure_ascii=False) + "\n")

            print(f"{test_id}: wrote eval line", file=sys.stderr)

    print(f"\nWrote {len(tests)} JSONL lines to {out_path} "
          f"({n_ok} harness calls succeeded).", file=sys.stderr)


if __name__ == "__main__":
    main()
