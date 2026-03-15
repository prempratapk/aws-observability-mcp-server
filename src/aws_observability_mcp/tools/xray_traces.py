"""AWS X-Ray Tracing MCP tools: get_traces and get_trace_summary."""
from __future__ import annotations
import json, logging
from datetime import datetime, timezone, timedelta
from typing import Any
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class GetTracesInput(BaseModel):
    filter_expression: str = Field(default="", description="X-Ray filter expression (e.g. 'service(\"my-service\") AND responsetime > 5'). Leave empty to get all traces.")
    start_time_iso: str = Field(default="", description="Start time ISO 8601. Defaults to 1 hour ago.")
    end_time_iso: str = Field(default="", description="End time ISO 8601. Defaults to now.")
    sampling: bool = Field(default=True, description="Whether to sample results (default True).")
    region: str = Field(default="us-east-1", description="AWS region.")

class GetTraceDetailsInput(BaseModel):
    trace_ids: list[str] = Field(description="List of X-Ray trace IDs to retrieve full segment details for (max 5).")
    region: str = Field(default="us-east-1", description="AWS region.")

TOOL_DEFINITIONS: list[Tool] = [
    Tool(name="get_traces", description="Retrieve AWS X-Ray trace summaries for a time window with optional filter expression. Use to identify slow requests, errors, and fault patterns across distributed services.", inputSchema=GetTracesInput.model_json_schema()),
    Tool(name="get_trace_details", description="Get full segment-level details for specific X-Ray trace IDs. Use after get_traces to drill into root cause of a specific slow or faulted request.", inputSchema=GetTraceDetailsInput.model_json_schema()),
]

def _iso_to_dt(iso: str) -> datetime:
    s = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _fmt_trace(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "Id": t.get("Id"),
        "Duration": t.get("Duration"),
        "ResponseTime": t.get("ResponseTime"),
        "HasFault": t.get("HasFault", False),
        "HasError": t.get("HasError", False),
        "HasThrottle": t.get("HasThrottle", False),
        "Http": t.get("Http", {}),
        "Users": t.get("Users", []),
        "ServiceIds": [{"Name": s.get("Name"), "Type": s.get("Type")} for s in t.get("ServiceIds", [])],
        "Annotations": t.get("Annotations", {}),
        "EntryPoint": t.get("EntryPoint", {}),
    }

async def _get_traces(arguments: dict[str, Any]) -> list[TextContent]:
    p = GetTracesInput(**arguments)
    xray = boto3.client("xray", region_name=p.region)
    now = datetime.now(tz=timezone.utc)
    end_dt = _iso_to_dt(p.end_time_iso) if p.end_time_iso else now
    start_dt = _iso_to_dt(p.start_time_iso) if p.start_time_iso else now - timedelta(hours=1)
    kwargs: dict[str, Any] = {
        "StartTime": start_dt,
        "EndTime": end_dt,
        "Sampling": p.sampling,
    }
    if p.filter_expression:
        kwargs["FilterExpression"] = p.filter_expression
    traces: list[dict[str, Any]] = []
    try:
        paginator = xray.get_paginator("get_trace_summaries")
        for page in paginator.paginate(**kwargs):
            for t in page.get("TraceSummaries", []):
                traces.append(_fmt_trace(t))
            if len(traces) >= 50:
                break
    except (ClientError, BotoCoreError) as exc:
        logger.error("X-Ray get_trace_summaries failed: %s", exc)
        return [TextContent(type="text", text=f"Error fetching traces: {exc}")]
    if not traces:
        return [TextContent(type="text", text=f"No traces found in region={p.region} for the given time range and filter.")]
    faults = sum(1 for t in traces if t["HasFault"])
    errors = sum(1 for t in traces if t["HasError"])
    avg_duration = sum(t["Duration"] or 0 for t in traces) / len(traces)
    summary = (
        f"X-Ray Trace Summary | region={p.region}\n"
        f"Time: {start_dt.isoformat()} -> {end_dt.isoformat()}\n"
        f"Total traces: {len(traces)} | Faults: {faults} | Errors: {errors} | Avg duration: {avg_duration:.3f}s\n"
        f"Filter: '{p.filter_expression or 'none'}'\n\n"
        f"Traces:\n{json.dumps(traces, indent=2, default=str)}"
    )
    return [TextContent(type="text", text=summary)]

async def _get_trace_details(arguments: dict[str, Any]) -> list[TextContent]:
    p = GetTraceDetailsInput(**arguments)
    xray = boto3.client("xray", region_name=p.region)
    ids = p.trace_ids[:5]
    try:
        response = xray.batch_get_traces(TraceIds=ids)
    except (ClientError, BotoCoreError) as exc:
        return [TextContent(type="text", text=f"Error fetching trace details: {exc}")]
    result: list[dict[str, Any]] = []
    for trace in response.get("Traces", []):
        segments = []
        for seg in trace.get("Segments", []):
            try:
                doc = json.loads(seg.get("Document", "{}"))
            except json.JSONDecodeError:
                doc = {}
            segments.append({"Id": seg.get("Id"), "name": doc.get("name"), "start_time": doc.get("start_time"), "end_time": doc.get("end_time"), "fault": doc.get("fault", False), "error": doc.get("error", False), "http": doc.get("http", {}), "aws": doc.get("aws", {}), "subsegments_count": len(doc.get("subsegments", []))})
        result.append({"TraceId": trace.get("Id"), "Duration": trace.get("Duration"), "Segments": segments})
    if not result:
        return [TextContent(type="text", text=f"No trace details found for IDs: {ids}")]
    return [TextContent(type="text", text=f"Trace details for {len(result)} trace(s):\n{json.dumps(result, indent=2, default=str)}")]

async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "get_traces": return await _get_traces(arguments)
    if name == "get_trace_details": return await _get_trace_details(arguments)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]
