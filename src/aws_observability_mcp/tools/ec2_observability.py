"""EC2 Observability MCP Tool."""
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent
from pydantic import BaseModel, Field


class GetEC2MetricsInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  instance_id: str = Field(description="EC2 instance ID")
  metric_name: str = Field(default="CPUUtilization", description="CloudWatch metric name")
  period: int = Field(default=300, description="Period in seconds")
  hours: int = Field(default=1, description="Hours of data to fetch")


class ListEC2InstancesInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  filters: list[dict[str, Any]] = Field(default=[], description="EC2 describe_instances filters")
  max_items: int = Field(default=50, description="Maximum number of instances to return")


class DescribeEC2InstanceInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  instance_id: str = Field(description="EC2 instance ID")


TOOL_DEFINITIONS = [
  {
    "name": "get_ec2_metrics",
    "description": "Get CloudWatch metrics for an EC2 instance (CPU, network, disk, status checks).",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "description": "AWS region", "default": "us-east-1"},
        "instance_id": {"type": "string", "description": "EC2 instance ID"},
        "metric_name": {"type": "string", "description": "CloudWatch metric name", "default": "CPUUtilization"},
        "period": {"type": "integer", "description": "Period in seconds", "default": 300},
        "hours": {"type": "integer", "description": "Hours of data to fetch", "default": 1},
      },
      "required": ["instance_id"],
    },
  },
  {
    "name": "list_ec2_instances",
    "description": "List EC2 instances in a region with their state, type, and tags.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "description": "AWS region", "default": "us-east-1"},
        "filters": {"type": "array", "description": "EC2 filters list", "default": []},
        "max_items": {"type": "integer", "description": "Max instances to return", "default": 50},
      },
      "required": [],
    },
  },
  {
    "name": "describe_ec2_instance",
    "description": "Get detailed information about a specific EC2 instance.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "description": "AWS region", "default": "us-east-1"},
        "instance_id": {"type": "string", "description": "EC2 instance ID"},
      },
      "required": ["instance_id"],
    },
  },
]


async def _get_ec2_metrics(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetEC2MetricsInput(**arguments)
  cw = boto3.client("cloudwatch", region_name=p.region)
  end = datetime.now(timezone.utc)
  start = end - timedelta(hours=p.hours)
  try:
    resp = cw.get_metric_statistics(
      Namespace="AWS/EC2",
      MetricName=p.metric_name,
      Dimensions=[{"Name": "InstanceId", "Value": p.instance_id}],
      StartTime=start,
      EndTime=end,
      Period=p.period,
      Statistics=["Average", "Maximum", "Minimum"],
    )
    datapoints = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    import json
    return [TextContent(type="text", text=f"EC2 metrics for {p.instance_id} ({p.metric_name}):\n{json.dumps(datapoints, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error fetching EC2 metrics: {exc}")]


async def _list_ec2_instances(arguments: dict[str, Any]) -> list[TextContent]:
  p = ListEC2InstancesInput(**arguments)
  ec2 = boto3.client("ec2", region_name=p.region)
  instances: list[dict[str, Any]] = []
  try:
    paginator = ec2.get_paginator("describe_instances")
    kwargs: dict[str, Any] = {}
    if p.filters:
      kwargs["Filters"] = p.filters
    for page in paginator.paginate(**kwargs):
      for reservation in page.get("Reservations", []):
        for inst in reservation.get("Instances", []):
          name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "")
          instances.append({
            "InstanceId": inst.get("InstanceId"),
            "InstanceType": inst.get("InstanceType"),
            "State": inst.get("State", {}).get("Name"),
            "PublicIpAddress": inst.get("PublicIpAddress"),
            "PrivateIpAddress": inst.get("PrivateIpAddress"),
            "LaunchTime": str(inst.get("LaunchTime")),
            "Name": name,
          })
          if len(instances) >= p.max_items:
            break
        if len(instances) >= p.max_items:
          break
      if len(instances) >= p.max_items:
        break
    import json
    if not instances:
      return [TextContent(type="text", text=f"No EC2 instances found in region={p.region}.")]
    return [TextContent(type="text", text=f"Found {len(instances)} EC2 instance(s) in region={p.region}:\n{json.dumps(instances, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error listing EC2 instances: {exc}")]


async def _describe_ec2_instance(arguments: dict[str, Any]) -> list[TextContent]:
  p = DescribeEC2InstanceInput(**arguments)
  ec2 = boto3.client("ec2", region_name=p.region)
  try:
    resp = ec2.describe_instances(InstanceIds=[p.instance_id])
    reservations = resp.get("Reservations", [])
    if not reservations:
      return [TextContent(type="text", text=f"Instance {p.instance_id} not found.")]
    inst = reservations[0]["Instances"][0]
    import json
    return [TextContent(type="text", text=f"EC2 instance details for {p.instance_id}:\n{json.dumps(inst, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error describing EC2 instance: {exc}")]


async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
  if name == "get_ec2_metrics": return await _get_ec2_metrics(arguments)
  if name == "list_ec2_instances": return await _list_ec2_instances(arguments)
  if name == "describe_ec2_instance": return await _describe_ec2_instance(arguments)
  return [TextContent(type="text", text=f"Unknown tool: {name}")]
