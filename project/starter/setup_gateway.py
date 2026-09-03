#!/usr/bin/env python3
"""Create the AgentCore Gateway and register the create_bug_report Lambda as a tool.

Usage: python setup_gateway.py
"""

import argparse
import json
import sys
import time

import boto3

REGION = "us-east-1"

TOOL_SCHEMA = {
    "name": "create_bug_report",
    "description": (
        "File a bug ticket in the engineering team's tracker. "
        "Call this only after the customer has provided a bug description, "
        "the steps to reproduce it, and their environment. "
        "Returns the new ticket's ID."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "What is broken, in the customer's words.",
            },
            "stepsToReproduce": {
                "type": "string",
                "description": "Steps to follow to reproduce the issue.",
            },
            "environment": {
                "type": "string",
                "description": "Customer's environment: browser, OS, device.",
            },
        },
        "required": ["description", "stepsToReproduce", "environment"],
    },
}


def stack_outputs(stack_name):
    cfn = boto3.client("cloudformation", region_name=REGION)
    stacks = cfn.describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def create_with_retry(fn, what, attempts=3, delay=10, **kwargs):
    for attempt in range(1, attempts + 1):
        try:
            return fn(**kwargs)
        except Exception as exc:
            if attempt == attempts:
                raise
            print(f"  {what} failed ({exc}); retrying in {delay}s "
                  f"(attempt {attempt}/{attempts})...")
            time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack-name", default="bug-report-tool-stack",
                        help="Name of the deployed cloudformation-tool.yaml stack.")
    parser.add_argument("--gateway-name", default=None,
                        help="Gateway name (default: <stack-name>-gateway).")
    parser.add_argument("--target-name", default="bugreports",
                        help="Gateway target name. Letters, digits, and "
                             "underscores ONLY — no dashes.")
    parser.add_argument("--config", default="agentcore_config.json",
                        help="Where to save the resulting configuration.")
    args = parser.parse_args()

    if not args.target_name.replace("_", "").isalnum():
        sys.exit("Target name may only contain letters, digits, and underscores.")

    gateway_name = args.gateway_name or f"{args.stack_name}-gateway"

    print(f"Reading outputs of stack '{args.stack_name}'...")
    outputs = stack_outputs(args.stack_name)
    lambda_arn = outputs["LambdaFunctionArn"]
    gateway_role_arn = outputs["GatewayRoleArn"]
    harness_role_arn = outputs["HarnessExecutionRoleArn"]
    table_name = outputs["BugReportsTableName"]

    acc = boto3.client("bedrock-agentcore-control", region_name=REGION)

    print(f"Creating gateway '{gateway_name}'...")
    gateway = create_with_retry(
        acc.create_gateway, "create_gateway",
        name=gateway_name,
        roleArn=gateway_role_arn,
        protocolType="MCP",
        authorizerType="AWS_IAM",
    )
    gateway_id = gateway["gatewayId"]
    gateway_arn = gateway["gatewayArn"]
    print(f"  gateway id:  {gateway_id}")
    print(f"  gateway arn: {gateway_arn}")

    for _ in range(30):
        status = acc.get_gateway(gatewayIdentifier=gateway_id).get("status", "READY")
        if status not in ("CREATING", "UPDATING"):
            break
        time.sleep(5)
    print(f"  gateway status: {status}")
    if status == "FAILED":
        sys.exit("Gateway creation failed — check the AgentCore console.")

    print(f"Adding Lambda target '{args.target_name}' "
          f"(tool: {TOOL_SCHEMA['name']})...")
    target = create_with_retry(
        acc.create_gateway_target, "create_gateway_target",
        gatewayIdentifier=gateway_id,
        name=args.target_name,
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": [TOOL_SCHEMA]},
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )
    target_id = target["targetId"]
    print(f"  target id: {target_id}")

    config = {
        "region": REGION,
        "stack_name": args.stack_name,
        "table_name": table_name,
        "lambda_arn": lambda_arn,
        "gateway_name": gateway_name,
        "gateway_id": gateway_id,
        "gateway_arn": gateway_arn,
        "gateway_target_id": target_id,
        "gateway_target_name": args.target_name,
        "harness_execution_role_arn": harness_role_arn,
    }
    with open(args.config, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print(f"\nDone. Saved configuration to {args.config}.")
    print("Next step: write your system prompt and run create_harness.py.")


if __name__ == "__main__":
    main()
