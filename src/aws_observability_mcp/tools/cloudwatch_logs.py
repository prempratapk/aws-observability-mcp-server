"""CloudWatch Logs MCP tool - query_logs via CloudWatch Logs Insights.

Exposes two MCP tools:
  - query_logs        : Run a CloudWatch Logs Insights query and return results.
  - list_log_groups   : List available CloudWatch log groups (optional prefix filter).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent, Tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input schemas (Pydantic)
# ---------------------------------------------------------------------------


class QueryLogsInput(BaseModel):
    """Input parameters for query_logs tool."""

    log_group_names: list[str] = Field(
        description="One or more CloudWatch log group names to query."
    )
    query_string: str = Field(
        description=(
            "CloudWatch Logs Insights query string. "
            "Example: 'fields @timestamp, @message | filter @message like /ERROR/ | limit 20'"
        )
    )
    start_time_iso: str = Field(
        description="Query start time in ISO 8601 format (e.g. '2026-03-14T00:00:00Z')."
    )
    end_time_iso: str = Field(
        description="Query end time in ISO 8601 format (e.g. '2026-03-14T23:59:59Z')."
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Maximum number of log records to return (1-10000, default 100).",
    )
    region: str = Field(
        default="us-east-1",
        description="AWS region where the log groups reside (default: us-east-1).",
    )


class ListLogGroupsInput(BaseModel):
    """Input parameters for list_log_groups tool."""

    prefix: str = Field(
        default="",
        description="Optional prefix to filter log group names (e.g. '/aws/lambda/').",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=50,
        description="Maximum number of log groups to return (1-50, default 50).",
    )
    region: str = Field(
        default="us-east-1",
        description="AWS region to list log groups from (default: us-east-1).",
    )


# ---------------------------------------------------------------------------
# MCP Tool definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[Tool] = [
    Tool(
        name="query_logs",
        description=(
            "Run a CloudWatch Logs Insights query against one or more log groups and return "
            "matching records. Use this to search for errors, filter by request ID, or "
            "aggregate metrics from application logs during incident investigation."
        ),
        inputSchema=QueryLogsInput.model_json_schema(),
    ),
    Tool(
        name="list_log_groups",
        description=(
            "List CloudWatch log groups in an AWS account and region, with optional prefix "
            "filtering. Use this to discover available log groups before running query_logs."
        ),
        inputSchema=ListLogGroupsInput.model_json_schema(),
    ),
]


# ---------------------------------------------------------------------------
# Helper: parse ISO timestamp -> epoch seconds
# ---------------------------------------------------------------------------


def _iso_to_epoch(iso_str: str) -> int:
    """Convert an ISO 8601 timestamp string to Unix epoch seconds."""
    from datetime import datetime, timezone

    # Support both 'Z' suffix and '+00:00'
    iso_str = iso_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _query_logs(arguments: dict[str, Any]) -> list[TextContent]:
    """Execute a CloudWatch Logs Insights query and poll until complete."""
    params = QueryLogsInput(**arguments)

    cw_logs = boto3.client("logs", region_name=params.region)

    start_epoch = _iso_to_epoch(params.start_time_iso)
    end_epoch = _iso_to_epoch(params.end_time_iso)

    logger.info(
        "Starting Logs Insights query on %d group(s), region=%s, limit=%d",
        len(params.log_group_names),
        params.region,
        params.limit,
    )

    try:
        start_resp = cw_logs.start_query(
            logGroupNames=params.log_group_names,
            startTime=start_epoch,
            endTime=end_epoch,
            queryString=params.query_string,
            limit=params.limit,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to start Logs Insights query: %s", exc)
        return [TextContent(type="text", text=f"Error starting query: {exc}")]

    query_id = start_resp["queryId"]
    logger.info("Query started, queryId=%s. Polling for results...", query_id)

    # Poll until the query completes (max 60 seconds)
    max_wait_secs = 60
    poll_interval = 2
    elapsed = 0
    results: list[dict[str, Any]] = []
    status = "Running"

    while elapsed < max_wait_secs:
        try:
            result_resp = cw_logs.get_query_results(queryId=query_id)
        except (ClientError, BotoCoreError) as exc:
            logger.error("Error polling query results: %s", exc)
            return [TextContent(type="text", text=f"Error polling query results: {exc}")]

        status = result_resp.get("status", "Unknown")
        if status in ("Complete", "Failed", "Cancelled", "Timeout"):
            results = result_resp.get("results", [])
            break

        time.sleep(poll_interval)
        elapsed += poll_interval

    if status != "Complete":
        return [
            TextContent(
                type="text",
                text=f"Query did not complete successfully. Final status: {status}. queryId={query_id}",
            )
        ]

    if not results:
        return [
            TextContent(
                type="text",
                text=(
                    f"Query completed successfully but returned 0 results.\n"
                    f"Log groups: {params.log_group_names}\n"
                    f"Time range: {params.start_time_iso} -> {params.end_time_iso}\n"
                    f"Query: {params.query_string}"
                ),
            )
        ]

    # Flatten each result row: [{field: value}, ...] -> dict
    rows = [
        {field["field"]: field["value"] for field in row}
        for row in results
    ]

    summary = (
        f"CloudWatch Logs Insights query returned {len(rows)} record(s).\n"
        f"Log groups: {params.log_group_names}\n"
        f"Time range: {params.start_time_iso} -> {params.end_time_iso}\n"
        f"Query: {params.query_string}\n"
        f"Region: {params.region}\n\n"
        f"Results:\n{json.dumps(rows, indent=2, default=str)}"
    )

    return [TextContent(type="text", text=summary)]


async def _list_log_groups(arguments: dict[str, Any]) -> list[TextContent]:
    """List CloudWatch log groups with optional prefix filter."""
    params = ListLogGroupsInput(**arguments)

    cw_logs = boto3.client("logs", region_name=params.region)

    kwargs: dict[str, Any] = {"limit": params.limit}
    if params.prefix:
        kwargs["logGroupNamePrefix"] = params.prefix

    try:
        response = cw_logs.describe_log_groups(**kwargs)
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to list log groups: %s", exc)
        return [TextContent(type="text", text=f"Error listing log groups: {exc}")]

    groups = response.get("logGroups", [])
    if not groups:
        return [
            TextContent(
                type="text",
                text=f"No log groups found in region={params.region} with prefix='{params.prefix}'.",
            )
        ]

    group_list = [
        {
            "logGroupName": g["logGroupName"],
            "storedBytes": g.get("storedBytes", 0),
            "retentionInDays": g.get("retentionInDays", "Never expire"),
            "creationTime": g.get("creationTime"),
        }
        for g in groups
    ]

    output = (
        f"Found {len(group_list)} log group(s) in region={params.region}\n"
        f"Prefix filter: '{params.prefix}'\n\n"
        f"{json.dumps(group_list, indent=2, default=str)}"
    )
    return [TextContent(type="text", text=output)]


# ---------------------------------------------------------------------------
# Unified dispatcher (called from server.py)
# ---------------------------------------------------------------------------


async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route tool calls to the correct CloudWatch Logs handler."""
    if name == "query_logs":
        return await _query_logs(arguments)
    if name == "list_log_groups":
        return await _list_log_groups(arguments)
    return [TextContent(type="text", text=f"Unknown cloudwatch_logs tool: {name}")]
