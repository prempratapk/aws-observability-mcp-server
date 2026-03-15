"""CloudWatch Alarms MCP tools: list_alarms and describe_alarm."""
from __future__ import annotations
import json, logging
from typing import Any, Literal
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class ListAlarmsInput(BaseModel):
    state: Literal["OK","ALARM","INSUFFICIENT_DATA","ALL"] = Field(default="ALARM", description="Filter by alarm state. ALARM shows actively firing alarms (default).")
    alarm_name_prefix: str = Field(default="", description="Optional prefix to filter alarm names.")
    alarm_types: list[Literal["CompositeAlarm","MetricAlarm"]] = Field(default=["MetricAlarm"], description="Alarm types to include.")
    max_results: int = Field(default=50, ge=1, le=100, description="Max alarms to return (1-100).")
    region: str = Field(default="us-east-1", description="AWS region.")

class DescribeAlarmInput(BaseModel):
    alarm_name: str = Field(description="Exact CloudWatch alarm name to describe.")
    region: str = Field(default="us-east-1", description="AWS region.")

TOOL_DEFINITIONS: list[Tool] = [
    Tool(name="list_alarms", description="List CloudWatch alarms by state (default ALARM), name prefix, and type. Use during incident triage to surface all firing alarms in a region.", inputSchema=ListAlarmsInput.model_json_schema()),
    Tool(name="describe_alarm", description="Get full configuration, current state, and recent history for a specific CloudWatch alarm by exact name.", inputSchema=DescribeAlarmInput.model_json_schema()),
]

def _fmt(a: dict[str, Any]) -> dict[str, Any]:
    return {"AlarmName": a.get("AlarmName"), "StateValue": a.get("StateValue"), "StateReason": a.get("StateReason",""), "StateUpdatedTimestamp": str(a.get("StateUpdatedTimestamp","")), "MetricName": a.get("MetricName",""), "Namespace": a.get("Namespace",""), "Dimensions": a.get("Dimensions",[]), "Threshold": a.get("Threshold"), "ComparisonOperator": a.get("ComparisonOperator",""), "EvaluationPeriods": a.get("EvaluationPeriods"), "AlarmActions": a.get("AlarmActions",[])}

async def _list_alarms(arguments: dict[str, Any]) -> list[TextContent]:
    p = ListAlarmsInput(**arguments)
    cw = boto3.client("cloudwatch", region_name=p.region)
    kwargs: dict[str, Any] = {"AlarmTypes": p.alarm_types, "MaxRecords": p.max_results}
    if p.state != "ALL": kwargs["StateValue"] = p.state
    if p.alarm_name_prefix: kwargs["AlarmNamePrefix"] = p.alarm_name_prefix
    try:
        r = cw.describe_alarms(**kwargs)
    except (ClientError, BotoCoreError) as exc:
        return [TextContent(type="text", text=f"Error listing alarms: {exc}")]
    alarms = [_fmt(a) for a in r.get("MetricAlarms",[]) + r.get("CompositeAlarms",[])]
    if not alarms:
        return [TextContent(type="text", text=f"No alarms in state={p.state} in region={p.region}.")]
    return [TextContent(type="text", text=f"Found {len(alarms)} alarm(s) [state={p.state}, region={p.region}]:\n{json.dumps(alarms, indent=2, default=str)}")]

async def _describe_alarm(arguments: dict[str, Any]) -> list[TextContent]:
    p = DescribeAlarmInput(**arguments)
    cw = boto3.client("cloudwatch", region_name=p.region)
    try:
        r = cw.describe_alarms(AlarmNames=[p.alarm_name])
    except (ClientError, BotoCoreError) as exc:
        return [TextContent(type="text", text=f"Error describing alarm: {exc}")]
    alarms = r.get("MetricAlarms",[]) + r.get("CompositeAlarms",[])
    if not alarms:
        return [TextContent(type="text", text=f"No alarm '{p.alarm_name}' found in region={p.region}.")]
    history: list[dict[str, Any]] = []
    try:
        hr = cw.describe_alarm_history(AlarmName=p.alarm_name, MaxRecords=10, ScanBy="TimestampDescending")
        history = [{"Timestamp": str(h.get("Timestamp","")), "Type": h.get("HistoryItemType",""), "Summary": h.get("HistorySummary","")} for h in hr.get("AlarmHistoryItems",[])]
    except (ClientError, BotoCoreError): pass
    out = {"alarm": _fmt(alarms[0]), "recent_history": history}
    return [TextContent(type="text", text=f"Alarm '{p.alarm_name}':\n{json.dumps(out, indent=2, default=str)}")]

async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "list_alarms": return await _list_alarms(arguments)
    if name == "describe_alarm": return await _describe_alarm(arguments)
    return [TextContent(type="text", text=f"Unknown tool: {name}")]
