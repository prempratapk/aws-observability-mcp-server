"""Cost Explorer MCP Tool."""
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent
from pydantic import BaseModel, Field


class GetCostAndUsageInput(BaseModel):
  start_date: str = Field(description="Start date in YYYY-MM-DD format")
  end_date: str = Field(description="End date in YYYY-MM-DD format")
  granularity: str = Field(default="MONTHLY", description="DAILY, MONTHLY, or HOURLY")
  group_by_service: bool = Field(default=True, description="Group results by AWS service")
  metrics: list[str] = Field(default=["UnblendedCost"], description="Cost metrics to retrieve")


class GetCostForecastInput(BaseModel):
  start_date: str = Field(description="Forecast start date in YYYY-MM-DD format")
  end_date: str = Field(description="Forecast end date in YYYY-MM-DD format")
  granularity: str = Field(default="MONTHLY", description="DAILY or MONTHLY")
  metric: str = Field(default="UNBLENDED_COST", description="Cost metric")


class GetTopServiceCostsInput(BaseModel):
  start_date: str = Field(description="Start date in YYYY-MM-DD format")
  end_date: str = Field(description="End date in YYYY-MM-DD format")
  top_n: int = Field(default=10, description="Number of top services to return")


TOOL_DEFINITIONS = [
  {
    "name": "get_cost_and_usage",
    "description": "Get AWS cost and usage data for a date range, optionally grouped by service.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
        "granularity": {"type": "string", "default": "MONTHLY", "enum": ["DAILY", "MONTHLY", "HOURLY"]},
        "group_by_service": {"type": "boolean", "default": True},
        "metrics": {"type": "array", "items": {"type": "string"}, "default": ["UnblendedCost"]},
      },
      "required": ["start_date", "end_date"],
    },
  },
  {
    "name": "get_cost_forecast",
    "description": "Get cost forecast for a future date range.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "start_date": {"type": "string", "description": "Forecast start date YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "Forecast end date YYYY-MM-DD"},
        "granularity": {"type": "string", "default": "MONTHLY"},
        "metric": {"type": "string", "default": "UNBLENDED_COST"},
      },
      "required": ["start_date", "end_date"],
    },
  },
  {
    "name": "get_top_service_costs",
    "description": "Get the top N AWS services by cost for a given date range.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
        "top_n": {"type": "integer", "default": 10},
      },
      "required": ["start_date", "end_date"],
    },
  },
]


async def _get_cost_and_usage(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetCostAndUsageInput(**arguments)
  ce = boto3.client("ce", region_name="us-east-1")
  try:
    kwargs: dict[str, Any] = {
      "TimePeriod": {"Start": p.start_date, "End": p.end_date},
      "Granularity": p.granularity,
      "Metrics": p.metrics,
    }
    if p.group_by_service:
      kwargs["GroupBy"] = [{"Type": "DIMENSION", "Key": "SERVICE"}]
    resp = ce.get_cost_and_usage(**kwargs)
    import json
    results = resp.get("ResultsByTime", [])
    return [TextContent(type="text", text=f"Cost and usage data ({p.start_date} to {p.end_date}):\n{json.dumps(results, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error getting cost and usage: {exc}")]


async def _get_cost_forecast(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetCostForecastInput(**arguments)
  ce = boto3.client("ce", region_name="us-east-1")
  try:
    resp = ce.get_cost_forecast(
      TimePeriod={"Start": p.start_date, "End": p.end_date},
      Granularity=p.granularity,
      Metric=p.metric,
    )
    import json
    total = resp.get("Total", {})
    forecast_results = resp.get("ForecastResultsByTime", [])
    result = {"Total": total, "ForecastResultsByTime": forecast_results}
    return [TextContent(type="text", text=f"Cost forecast ({p.start_date} to {p.end_date}):\n{json.dumps(result, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error getting cost forecast: {exc}")]


async def _get_top_service_costs(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetTopServiceCostsInput(**arguments)
  ce = boto3.client("ce", region_name="us-east-1")
  try:
    resp = ce.get_cost_and_usage(
      TimePeriod={"Start": p.start_date, "End": p.end_date},
      Granularity="MONTHLY",
      Metrics=["UnblendedCost"],
      GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    service_costs: dict[str, float] = {}
    for period in resp.get("ResultsByTime", []):
      for group in period.get("Groups", []):
        service = group["Keys"][0]
        amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
        service_costs[service] = service_costs.get(service, 0.0) + amount
    sorted_services = sorted(service_costs.items(), key=lambda x: x[1], reverse=True)[:p.top_n]
    import json
    top_services = [{"Service": svc, "TotalCost": round(cost, 4)} for svc, cost in sorted_services]
    return [TextContent(type="text", text=f"Top {p.top_n} AWS services by cost ({p.start_date} to {p.end_date}):\n{json.dumps(top_services)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error getting top service costs: {exc}")]


async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
  if name == "get_cost_and_usage": return await _get_cost_and_usage(arguments)
  if name == "get_cost_forecast": return await _get_cost_forecast(arguments)
  if name == "get_top_service_costs": return await _get_top_service_costs(arguments)
  return [TextContent(type="text", text=f"Unknown tool: {name}")]
