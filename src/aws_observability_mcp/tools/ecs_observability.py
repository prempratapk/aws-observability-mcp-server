"""ECS/EKS Observability MCP Tool."""
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent
from pydantic import BaseModel, Field


class ListECSClustersInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  max_items: int = Field(default=50, description="Maximum clusters to return")


class ListECSServicesInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  cluster: str = Field(description="ECS cluster name or ARN")
  max_items: int = Field(default=50, description="Maximum services to return")


class GetECSMetricsInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  cluster_name: str = Field(description="ECS cluster name")
  service_name: str = Field(description="ECS service name")
  metric_name: str = Field(default="CPUUtilization", description="CloudWatch metric name")
  period: int = Field(default=300, description="Period in seconds")
  hours: int = Field(default=1, description="Hours of data to fetch")


class ListEKSClustersInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")


TOOL_DEFINITIONS = [
  {
    "name": "list_ecs_clusters",
    "description": "List ECS clusters in a region.",
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
    "name": "list_ecs_services",
    "description": "List ECS services in a cluster with status and task count.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "cluster": {"type": "string", "description": "ECS cluster name or ARN"},
        "max_items": {"type": "integer", "default": 50},
      },
      "required": ["cluster"],
    },
  },
  {
    "name": "get_ecs_metrics",
    "description": "Get CloudWatch metrics for an ECS service (CPU, memory utilization).",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "cluster_name": {"type": "string", "description": "ECS cluster name"},
        "service_name": {"type": "string", "description": "ECS service name"},
        "metric_name": {"type": "string", "default": "CPUUtilization"},
        "period": {"type": "integer", "default": 300},
        "hours": {"type": "integer", "default": 1},
      },
      "required": ["cluster_name", "service_name"],
    },
  },
  {
    "name": "list_eks_clusters",
    "description": "List EKS clusters in a region.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
      },
      "required": [],
    },
  },
]


async def _list_ecs_clusters(arguments: dict[str, Any]) -> list[TextContent]:
  p = ListECSClustersInput(**arguments)
  ecs = boto3.client("ecs", region_name=p.region)
  try:
    paginator = ecs.get_paginator("list_clusters")
    arns: list[str] = []
    for page in paginator.paginate():
      arns.extend(page.get("clusterArns", []))
      if len(arns) >= p.max_items:
        break
    arns = arns[:p.max_items]
    if not arns:
      return [TextContent(type="text", text=f"No ECS clusters found in region={p.region}.")]
    details = ecs.describe_clusters(clusters=arns).get("clusters", [])
    import json
    clusters = [{"clusterName": c.get("clusterName"), "status": c.get("status"), "activeServicesCount": c.get("activeServicesCount"), "runningTasksCount": c.get("runningTasksCount")} for c in details]
    return [TextContent(type="text", text=f"Found {len(clusters)} ECS cluster(s) in region={p.region}:\n{json.dumps(clusters)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error listing ECS clusters: {exc}")]


async def _list_ecs_services(arguments: dict[str, Any]) -> list[TextContent]:
  p = ListECSServicesInput(**arguments)
  ecs = boto3.client("ecs", region_name=p.region)
  try:
    paginator = ecs.get_paginator("list_services")
    arns: list[str] = []
    for page in paginator.paginate(cluster=p.cluster):
      arns.extend(page.get("serviceArns", []))
      if len(arns) >= p.max_items:
        break
    arns = arns[:p.max_items]
    if not arns:
      return [TextContent(type="text", text=f"No ECS services found in cluster={p.cluster}.")]
    details = ecs.describe_services(cluster=p.cluster, services=arns).get("services", [])
    import json
    services = [{"serviceName": s.get("serviceName"), "status": s.get("status"), "desiredCount": s.get("desiredCount"), "runningCount": s.get("runningCount"), "pendingCount": s.get("pendingCount")} for s in details]
    return [TextContent(type="text", text=f"Found {len(services)} ECS service(s) in cluster={p.cluster}:\n{json.dumps(services)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error listing ECS services: {exc}")]


async def _get_ecs_metrics(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetECSMetricsInput(**arguments)
  cw = boto3.client("cloudwatch", region_name=p.region)
  end = datetime.now(timezone.utc)
  start = end - timedelta(hours=p.hours)
  try:
    resp = cw.get_metric_statistics(
      Namespace="AWS/ECS",
      MetricName=p.metric_name,
      Dimensions=[
        {"Name": "ClusterName", "Value": p.cluster_name},
        {"Name": "ServiceName", "Value": p.service_name},
      ],
      StartTime=start,
      EndTime=end,
      Period=p.period,
      Statistics=["Average", "Maximum"],
    )
    datapoints = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    import json
    return [TextContent(type="text", text=f"ECS metrics for {p.cluster_name}/{p.service_name} ({p.metric_name}):\n{json.dumps(datapoints, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error fetching ECS metrics: {exc}")]


async def _list_eks_clusters(arguments: dict[str, Any]) -> list[TextContent]:
  p = ListEKSClustersInput(**arguments)
  eks = boto3.client("eks", region_name=p.region)
  try:
    paginator = eks.get_paginator("list_clusters")
    names: list[str] = []
    for page in paginator.paginate():
      names.extend(page.get("clusters", []))
    if not names:
      return [TextContent(type="text", text=f"No EKS clusters found in region={p.region}.")]
    details = []
    for name in names:
      try:
        cluster = eks.describe_cluster(name=name).get("cluster", {})
        details.append({"name": cluster.get("name"), "status": cluster.get("status"), "version": cluster.get("version"), "endpoint": cluster.get("endpoint")})
      except Exception:
        details.append({"name": name})
    import json
    return [TextContent(type="text", text=f"Found {len(details)} EKS cluster(s) in region={p.region}:\n{json.dumps(details)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error listing EKS clusters: {exc}")]


async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
  if name == "list_ecs_clusters": return await _list_ecs_clusters(arguments)
  if name == "list_ecs_services": return await _list_ecs_services(arguments)
  if name == "get_ecs_metrics": return await _get_ecs_metrics(arguments)
  if name == "list_eks_clusters": return await _list_eks_clusters(arguments)
  return [TextContent(type="text", text=f"Unknown tool: {name}")]
