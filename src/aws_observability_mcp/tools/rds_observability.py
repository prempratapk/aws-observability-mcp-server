"""RDS Observability MCP Tool."""
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent
from pydantic import BaseModel, Field


class GetRDSMetricsInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  db_instance_identifier: str = Field(description="RDS DB instance identifier")
  metric_name: str = Field(default="CPUUtilization", description="CloudWatch metric name")
  period: int = Field(default=300, description="Period in seconds")
  hours: int = Field(default=1, description="Hours of data to fetch")


class ListRDSInstancesInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  max_items: int = Field(default=50, description="Maximum number of instances to return")


class DescribeRDSInstanceInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  db_instance_identifier: str = Field(description="RDS DB instance identifier")


TOOL_DEFINITIONS = [
  {
    "name": "get_rds_metrics",
    "description": "Get CloudWatch metrics for an RDS instance (CPU, connections, storage, IOPS).",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "db_instance_identifier": {"type": "string", "description": "RDS DB instance identifier"},
        "metric_name": {"type": "string", "default": "CPUUtilization"},
        "period": {"type": "integer", "default": 300},
        "hours": {"type": "integer", "default": 1},
      },
      "required": ["db_instance_identifier"],
    },
  },
  {
    "name": "list_rds_instances",
    "description": "List RDS DB instances in a region with status, engine, and size info.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "max_items": {"type": "integer", "default": 50},
      },
      "required": [],
    },
  },
  {
    "name": "describe_rds_instance",
    "description": "Get detailed information about a specific RDS DB instance.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "db_instance_identifier": {"type": "string", "description": "RDS DB instance identifier"},
      },
      "required": ["db_instance_identifier"],
    },
  },
]


async def _get_rds_metrics(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetRDSMetricsInput(**arguments)
  cw = boto3.client("cloudwatch", region_name=p.region)
  end = datetime.now(timezone.utc)
  start = end - timedelta(hours=p.hours)
  try:
    resp = cw.get_metric_statistics(
      Namespace="AWS/RDS",
      MetricName=p.metric_name,
      Dimensions=[{"Name": "DBInstanceIdentifier", "Value": p.db_instance_identifier}],
      StartTime=start,
      EndTime=end,
      Period=p.period,
      Statistics=["Average", "Maximum", "Minimum"],
    )
    datapoints = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    import json
    return [TextContent(type="text", text=f"RDS metrics for {p.db_instance_identifier} ({p.metric_name}):\n{json.dumps(datapoints, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error fetching RDS metrics: {exc}")]


async def _list_rds_instances(arguments: dict[str, Any]) -> list[TextContent]:
  p = ListRDSInstancesInput(**arguments)
  rds = boto3.client("rds", region_name=p.region)
  instances: list[dict[str, Any]] = []
  try:
    paginator = rds.get_paginator("describe_db_instances")
    for page in paginator.paginate():
      for db in page.get("DBInstances", []):
        instances.append({
          "DBInstanceIdentifier": db.get("DBInstanceIdentifier"),
          "DBInstanceClass": db.get("DBInstanceClass"),
          "Engine": db.get("Engine"),
          "EngineVersion": db.get("EngineVersion"),
          "DBInstanceStatus": db.get("DBInstanceStatus"),
          "MultiAZ": db.get("MultiAZ"),
          "AllocatedStorage": db.get("AllocatedStorage"),
          "Endpoint": db.get("Endpoint", {}).get("Address"),
        })
        if len(instances) >= p.max_items:
          break
      if len(instances) >= p.max_items:
        break
    import json
    if not instances:
      return [TextContent(type="text", text=f"No RDS instances found in region={p.region}.")]
    return [TextContent(type="text", text=f"Found {len(instances)} RDS instance(s) in region={p.region}:\n{json.dumps(instances, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error listing RDS instances: {exc}")]


async def _describe_rds_instance(arguments: dict[str, Any]) -> list[TextContent]:
  p = DescribeRDSInstanceInput(**arguments)
  rds = boto3.client("rds", region_name=p.region)
  try:
    resp = rds.describe_db_instances(DBInstanceIdentifier=p.db_instance_identifier)
    dbs = resp.get("DBInstances", [])
    if not dbs:
      return [TextContent(type="text", text=f"RDS instance {p.db_instance_identifier} not found.")]
    import json
    return [TextContent(type="text", text=f"RDS instance details for {p.db_instance_identifier}:\n{json.dumps(dbs[0], default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error describing RDS instance: {exc}")]


async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
  if name == "get_rds_metrics": return await _get_rds_metrics(arguments)
  if name == "list_rds_instances": return await _list_rds_instances(arguments)
  if name == "describe_rds_instance": return await _describe_rds_instance(arguments)
  return [TextContent(type="text", text=f"Unknown tool: {name}")]
