"""AWS Health MCP tool: get_health_events and describe_health_event."""
from __future__ import annotations
import json, logging
from typing import Any, Literal
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# NOTE: AWS Health API is only available in us-east-1 endpoint globally.
_HEALTH_REGION = "us-east-1"

class GetHealthEventsInput(BaseModel):
    event_status_codes: list[Literal["open","closed","upcoming"]] = Field(
        default=["open"],
        description="Event status filter: open (active), closed (resolved), upcoming.",
    )
    services: list[str] = Field(
        default=[],
        description="AWS service codes to filter (e.g. ['EC2','LAMBDA','DynamoDB']). Empty = all services.",
    )
    regions: list[str] = Field(
        default=[],
        description="AWS regions to filter (e.g. ['us-east-1','us-west-2']). Empty = all regions.",
    )
    event_type_categories: list[Literal["issue","accountNotification","scheduledChange","investigation"]] = Field(
        default=["issue","investigation"],
        description="Event type category filter.",
    )
    max_results: int = Field(default=20, ge=1, le=100, description="Max events to return.")

class DescribeHealthEventInput(BaseModel):
    event_arn: str = Field(description="Full ARN of the AWS Health event to describe.")

TOOL_DEFINITIONS: list[Tool] = [
    Tool(
        name="get_health_events",
        description="List active AWS Health events (service disruptions, maintenance, notifications) filtered by status, service, region, and category. Use during incidents to check for AWS-side outages.",
        inputSchema=GetHealthEventsInput.model_json_schema(),
    ),
    Tool(
        name="describe_health_event",
        description="Get full details and affected entities for a specific AWS Health event by ARN.",
        inputSchema=DescribeHealthEventInput.model_json_schema(),
    ),
]

def _fmt_event(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "arn": e.get("arn"),
        "service": e.get("service"),
        "eventTypeCode": e.get("eventTypeCode"),
        "eventTypeCategory": e.get("eventTypeCategory"),
        "region": e.get("region"),
        "availabilityZone": e.get("availabilityZone"),
        "startTime": str(e.get("startTime","")),
        "endTime": str(e.get("endTime","")),
        "lastUpdatedTime": str(e.get("lastUpdatedTime","")),
        "statusCode": e.get("statusCode"),
        "eventScopeCode": e.get("eventScopeCode"),
    }

async def _get_health_events(arguments: dict[str, Any]) -> list[TextContent]:
    p = GetHealthEventsInput(**arguments)
    # Health API only available in us-east-1
    health = boto3.client("health", region_name=_HEALTH_REGION)
    filter_dict: dict[str, Any] = {
        "eventStatusCodes": p.event_status_codes,
        "eventTypeCategories": p.event_type_categories,
    }
    if p.services:
        filter_dict["services"] = p.services
    if p.regions:
        filter_dict["regions"] = p.regions
    events: list[dict[str, Any]] = []
    try:
        paginator = health.get_paginator("describe_events")
        for page in paginator.paginate(filter=filter_dict, maxResults=p.max_results):
            for e in page.get("events", []):
                events.append(_fmt_event(e))
            if len(events) >= p.max_results:
                break
    except ClientError as exc:
        err_code = exc.response["Error"]["Code"]
        if err_code == "SubscriptionRequiredException":
            return [TextContent(type="text", text="AWS Health API requires a Business or Enterprise Support plan. This account does not have access to AWS Health API.")]
        return [TextContent(type="text", text=f"Error fetching health events: {exc}")]
    except BotoCoreError as exc:
        return [TextContent(type="text", text=f"Error fetching health events: {exc}")]
    if not events:
        return [TextContent(type="text", text=f"No AWS Health events found for status={p.event_status_codes}, services={p.services or 'all'}, regions={p.regions or 'all'}.")]
    issues = [e for e in events if e["eventTypeCategory"] in ("issue","investigation")]
    scheduled = [e for e in events if e["eventTypeCategory"] == "scheduledChange"]
    summary = (
        f"AWS Health Events | status={p.event_status_codes}\n"
        f"Total: {len(events)} | Issues/Investigations: {len(issues)} | Scheduled changes: {len(scheduled)}\n"
        f"Service filter: {p.services or 'all'} | Region filter: {p.regions or 'all'}\n\n"
        f"{json.dumps(events, indent=2, default=str)}"
    )
    return [TextContent(type="text", text=summary)]

async def _describe_health_event(arguments: dict[str, Any]) -> list[TextContent]:
    p = DescribeHealthEventInput(**arguments)
    health = boto3.client("health", region_name=_HEALTH_REGION)
    try:
        # Get event details
        details_resp = health.describe_event_details(eventArns=[p.event_arn])
        details = details_resp.get("successfulSet", [])
        failed = details_resp.get("failedSet", [])
        if failed:
            return [TextContent(type="text", text=f"Failed to get event details: {failed}")]
        if not details:
            return [TextContent(type="text", text=f"No details found for event ARN: {p.event_arn}")]
        event_detail = details[0]
        event = _fmt_event(event_detail.get("event", {}))
        description = event_detail.get("eventDescription", {}).get("latestDescription", "")
        # Get affected entities
        entities: list[dict[str, Any]] = []
        try:
            ent_resp = health.describe_affected_entities(filter={"eventArns": [p.event_arn]})
            entities = [{"entityArn": e.get("entityArn"), "entityValue": e.get("entityValue"), "statusCode": e.get("statusCode"), "lastUpdatedTime": str(e.get("lastUpdatedTime",""))} for e in ent_resp.get("entities", [])[:20]]
        except (ClientError, BotoCoreError): pass
        out = {"event": event, "description": description, "affected_entities": entities}
        return [TextContent(type="text", text=f"AWS Health Event Details:\n{json.dumps(out, indent=2, default=str)}")]
    except ClientError as exc:
        err_code = exc.response["Error"]["Code"]
        if err_code == "SubscriptionRequiredException":
            return [TextContent(type="text", text="AWS Health API requires Business or Enterprise Support plan.")]
        return [TextContent(type="text", text=f"Error: {exc}")]
    except BotoCoreError as exc:
        return [TextContent(type="text", text=f"Error: {exc}")]

async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "get_health_events": return await _get_health_events(arguments)
    if name == "describe_health_event": return await _describe_health_event(arguments)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]
