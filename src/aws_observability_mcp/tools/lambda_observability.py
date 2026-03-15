"""Lambda Observability MCP tools: get_lambda_metrics and list_lambda_functions."""
from __future__ import annotations
import json, logging
from datetime import datetime, timezone, timedelta
from typing import Any
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class GetLambdaMetricsInput(BaseModel):
    function_name: str = Field(description="Lambda function name or ARN.")
    start_time_iso: str = Field(default="", description="Start time ISO 8601. Defaults to 1 hour ago.")
    end_time_iso: str = Field(default="", description="End time ISO 8601. Defaults to now.")
    period_seconds: int = Field(default=300, ge=60, le=86400, description="Metric aggregation period in seconds (min 60, default 300).")
    region: str = Field(default="us-east-1", description="AWS region.")

class ListLambdaFunctionsInput(BaseModel):
    function_version: str = Field(default="ALL", description="Version to list: ALL or a specific version.")
    region: str = Field(default="us-east-1", description="AWS region.")
    max_items: int = Field(default=50, ge=1, le=100, description="Max functions to return.")

TOOL_DEFINITIONS: list[Tool] = [
    Tool(name="get_lambda_metrics", description="Get key CloudWatch metrics for a Lambda function: Invocations, Errors, Duration (avg/p99), Throttles, ConcurrentExecutions, and cold start proxy. Returns per-period data points and summary stats.", inputSchema=GetLambdaMetricsInput.model_json_schema()),
    Tool(name="list_lambda_functions", description="List Lambda functions in a region with runtime, memory, timeout, last modified, and code size. Use to discover functions before querying metrics.", inputSchema=ListLambdaFunctionsInput.model_json_schema()),
]

_METRICS = [
    ("Invocations", "Sum"),
    ("Errors", "Sum"),
    ("Duration", "Average"),
    ("Duration", "p99"),
    ("Throttles", "Sum"),
    ("ConcurrentExecutions", "Maximum"),
    ("InitDuration", "Average"),
]

def _iso_to_dt(iso: str) -> datetime:
    s = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

async def _get_lambda_metrics(arguments: dict[str, Any]) -> list[TextContent]:
    p = GetLambdaMetricsInput(**arguments)
    cw = boto3.client("cloudwatch", region_name=p.region)
    now = datetime.now(tz=timezone.utc)
    end_dt = _iso_to_dt(p.end_time_iso) if p.end_time_iso else now
    start_dt = _iso_to_dt(p.start_time_iso) if p.start_time_iso else now - timedelta(hours=1)
    queries = []
    for i, (metric_name, stat) in enumerate(_METRICS):
        # p99 requires extended statistics
        stat_key = "ExtendedStatistics" if stat.startswith("p") else "Statistics"
        query: dict[str, Any] = {
            "Id": f"m{i}",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/Lambda",
                    "MetricName": metric_name,
                    "Dimensions": [{"Name": "FunctionName", "Value": p.function_name}],
                },
                "Period": p.period_seconds,
                stat_key: [stat] if stat_key == "Statistics" else None,
            },
            "ReturnData": True,
        }
        if stat_key == "ExtendedStatistics":
            query["MetricStat"]["ExtendedStatistics"] = [stat]
        else:
            query["MetricStat"]["Statistics"] = [stat]
            del query["MetricStat"][stat_key] if stat_key not in ("Statistics","ExtendedStatistics") else None
        queries.append(query)
    try:
        response = cw.get_metric_data(
            MetricDataQueries=queries,
            StartTime=start_dt,
            EndTime=end_dt,
            ScanBy="TimestampAscending",
        )
    except (ClientError, BotoCoreError) as exc:
        return [TextContent(type="text", text=f"Error fetching Lambda metrics: {exc}")]
    results: dict[str, Any] = {}
    for r in response.get("MetricDataResults", []):
        idx = int(r["Id"][1:])
        metric_name, stat = _METRICS[idx]
        key = f"{metric_name}_{stat}"
        values = r.get("Values", [])
        results[key] = {
            "values": values,
            "sum": sum(values),
            "avg": sum(values) / len(values) if values else 0,
            "max": max(values) if values else 0,
            "min": min(values) if values else 0,
            "datapoints": len(values),
        }
    error_rate = 0.0
    inv = results.get("Invocations_Sum", {})
    err = results.get("Errors_Sum", {})
    if inv.get("sum", 0) > 0:
        error_rate = (err.get("sum", 0) / inv["sum"]) * 100
    summary = (
        f"Lambda Metrics: {p.function_name} | region={p.region}\n"
        f"Time: {start_dt.isoformat()} -> {end_dt.isoformat()} | Period: {p.period_seconds}s\n"
        f"Invocations: {inv.get('sum',0):.0f} | Errors: {err.get('sum',0):.0f} | Error rate: {error_rate:.2f}%\n"
        f"Avg Duration: {results.get('Duration_Average',{}).get('avg',0):.2f}ms | p99 Duration: {results.get('Duration_p99',{}).get('avg',0):.2f}ms\n"
        f"Throttles: {results.get('Throttles_Sum',{}).get('sum',0):.0f} | Max Concurrency: {results.get('ConcurrentExecutions_Maximum',{}).get('max',0):.0f}\n"
        f"Avg Cold Start (InitDuration): {results.get('InitDuration_Average',{}).get('avg',0):.2f}ms\n\n"
        f"Raw metrics:\n{json.dumps(results, indent=2, default=str)}"
    )
    return [TextContent(type="text", text=summary)]

async def _list_lambda_functions(arguments: dict[str, Any]) -> list[TextContent]:
    p = ListLambdaFunctionsInput(**arguments)
    lam = boto3.client("lambda", region_name=p.region)
    functions: list[dict[str, Any]] = []
    try:
        paginator = lam.get_paginator("list_functions")
        for page in paginator.paginate(FunctionVersion=p.function_version):
            for fn in page.get("Functions", []):
                functions.append({
                    "FunctionName": fn.get("FunctionName"),
                    "Runtime": fn.get("Runtime"),
                    "MemorySize": fn.get("MemorySize"),
                    "Timeout": fn.get("Timeout"),
                    "LastModified": fn.get("LastModified"),
                    "CodeSize": fn.get("CodeSize"),
                    "Handler": fn.get("Handler"),
                    "Description": fn.get("Description",""),
                    "Architectures": fn.get("Architectures",[]),
                })
            if len(functions) >= p.max_items:
                break
    except (ClientError, BotoCoreError) as exc:
        return [TextContent(type="text", text=f"Error listing Lambda functions: {exc}")]
    if not functions:
        return [TextContent(type="text", text=f"No Lambda functions found in region={p.region}.")]
    return [TextContent(type="text", text=f"Found {len(functions)} Lambda function(s) in region={p.region}:\n{json.dumps(functions, indent=2, default=str)}")]

async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "get_lambda_metrics": return await _get_lambda_metrics(arguments)
    if name == "list_lambda_functions": return await _list_lambda_functions(arguments)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]
