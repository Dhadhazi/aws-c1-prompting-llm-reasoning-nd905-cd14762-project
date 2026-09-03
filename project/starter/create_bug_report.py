"""Lambda function behind the create_bug_report tool. Stores bug reports in DynamoDB.

This code is mirrored in cloudformation-tool.yaml - keep both in sync.
"""

import json
import os
import uuid
from datetime import datetime, timezone
import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _tool_name(context):
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None) or {}
    name = custom.get("bedrockAgentCoreToolName", "")
    return name.split("___")[-1] if "___" in name else name


def lambda_handler(event, context):
    print("EVENT:", json.dumps(event, default=str))

    tool_name = _tool_name(context)
    print("TOOL NAME:", tool_name)
    if tool_name and tool_name != "create_bug_report":
        return {"error": f"unsupported tool: {tool_name}"}

    if not isinstance(event, dict):
        return {"error": "unexpected event shape", "event": str(event)}

    description = str(event.get("description") or "").strip()
    steps = str(event.get("stepsToReproduce") or "").strip()
    environment = str(event.get("environment") or "").strip()

    missing = [name for name, value in [("description", description),
                                        ("stepsToReproduce", steps),
                                        ("environment", environment)] if not value]
    if missing:
        return {"error": "missing required field(s): " + ", ".join(missing)
                         + ". Ask the customer for them before filing the ticket."}

    ticket_id = str(uuid.uuid4())
    item = {
        "ticketId": ticket_id,
        "description": description,
        "stepsToReproduce": steps,
        "environment": environment,
        "status": "OPEN",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    table.put_item(Item=item)

    return {"ticketId": ticket_id, "status": "OPEN"}
