#!/usr/bin/env python3
"""Delete the harness, gateway target and gateway created for this project.

Usage: python cleanup_agentcore.py
"""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def is_not_found(exc):
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in ("ResourceNotFoundException", "NotFoundException", "404")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="agentcore_config.json",
                        help="Config file written by the setup scripts.")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        sys.exit(f"{args.config} not found — nothing to clean up.")
    config = json.loads(config_path.read_text(encoding="utf-8"))

    acc = boto3.client("bedrock-agentcore-control", region_name=config["region"])

    harness_id = config.get("harness_id")
    if harness_id:
        print(f"Deleting harness {config.get('harness_name', harness_id)}...")
        try:
            acc.delete_harness(harnessId=harness_id)
            for _ in range(24):
                try:
                    acc.get_harness(harnessId=harness_id)
                except ClientError as exc:
                    if is_not_found(exc):
                        break
                    raise
                time.sleep(10)
            print("  harness deleted.")
        except ClientError as exc:
            if is_not_found(exc):
                print("  already gone.")
            else:
                raise

    gateway_id = config.get("gateway_id")
    if gateway_id:
        target_id = config.get("gateway_target_id")
        if target_id:
            print(f"Deleting gateway target {target_id}...")
            try:
                acc.delete_gateway_target(gatewayIdentifier=gateway_id,
                                          targetId=target_id)
                time.sleep(5)
                print("  target deleted.")
            except ClientError as exc:
                if is_not_found(exc):
                    print("  already gone.")
                else:
                    raise

        print(f"Deleting gateway {config.get('gateway_name', gateway_id)}...")
        try:
            acc.delete_gateway(gatewayIdentifier=gateway_id)
            print("  gateway deleted.")
        except ClientError as exc:
            if is_not_found(exc):
                print("  already gone.")
            else:
                raise

    print("\nAgentCore cleanup complete. Now delete the CloudFormation stacks")
    print("(empty the evaluation bucket first, or the testing stack delete fails):")
    print(f"  aws cloudformation delete-stack --stack-name {config.get('stack_name', 'bug-report-tool-stack')} --region {config['region']}")
    print(f"  aws s3 rm s3://udacity-agentic-engineer-c1-eval-<YOUR_ACCOUNT_ID> --recursive")
    print(f"  aws cloudformation delete-stack --stack-name bug-report-testing-stack --region {config['region']}")


if __name__ == "__main__":
    main()
