"""SNS/SQS Observability MCP Tool."""
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from mcp.types import TextContent
from pydantic import BaseModel, Field


class ListSNSTopicsInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  max_items: int = Field(default=50, description="Maximum topics to return")


class GetSNSTopicAttributesInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  topic_arn: str = Field(description="SNS topic ARN")


class ListSQSQueuesInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  queue_name_prefix: str = Field(default="", description="Optional queue name prefix filter")
  max_items: int = Field(default=50, description="Maximum queues to return")


class GetSQSQueueAttributesInput(BaseModel):
  region: str = Field(default="us-east-1", description="AWS region")
  queue_url: str = Field(description="SQS queue URL")


TOOL_DEFINITIONS = [
  {
    "name": "list_sns_topics",
    "description": "List SNS topics in a region.",
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
    "name": "get_sns_topic_attributes",
    "description": "Get attributes and subscription info for an SNS topic.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "topic_arn": {"type": "string", "description": "SNS topic ARN"},
      },
      "required": ["topic_arn"],
    },
  },
  {
    "name": "list_sqs_queues",
    "description": "List SQS queues in a region with message counts and configuration.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "queue_name_prefix": {"type": "string", "default": ""},
        "max_items": {"type": "integer", "default": 50},
      },
      "required": [],
    },
  },
  {
    "name": "get_sqs_queue_attributes",
    "description": "Get detailed attributes for an SQS queue including message counts and DLQ info.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "region": {"type": "string", "default": "us-east-1"},
        "queue_url": {"type": "string", "description": "SQS queue URL"},
      },
      "required": ["queue_url"],
    },
  },
]


async def _list_sns_topics(arguments: dict[str, Any]) -> list[TextContent]:
  p = ListSNSTopicsInput(**arguments)
  sns = boto3.client("sns", region_name=p.region)
  try:
    paginator = sns.get_paginator("list_topics")
    topics: list[str] = []
    for page in paginator.paginate():
      for t in page.get("Topics", []):
        topics.append(t.get("TopicArn", ""))
        if len(topics) >= p.max_items:
          break
      if len(topics) >= p.max_items:
        break
    if not topics:
      return [TextContent(type="text", text=f"No SNS topics found in region={p.region}.")]
    import json
    return [TextContent(type="text", text=f"Found {len(topics)} SNS topic(s) in region={p.region}:\n{json.dumps(topics)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error listing SNS topics: {exc}")]


async def _get_sns_topic_attributes(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetSNSTopicAttributesInput(**arguments)
  sns = boto3.client("sns", region_name=p.region)
  try:
    attrs = sns.get_topic_attributes(TopicArn=p.topic_arn).get("Attributes", {})
    subs_resp = sns.list_subscriptions_by_topic(TopicArn=p.topic_arn)
    subs = [{"Protocol": s.get("Protocol"), "Endpoint": s.get("Endpoint"), "SubscriptionArn": s.get("SubscriptionArn")} for s in subs_resp.get("Subscriptions", [])]
    import json
    result = {"Attributes": attrs, "Subscriptions": subs}
    return [TextContent(type="text", text=f"SNS topic details for {p.topic_arn}:\n{json.dumps(result, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error getting SNS topic attributes: {exc}")]


async def _list_sqs_queues(arguments: dict[str, Any]) -> list[TextContent]:
  p = ListSQSQueuesInput(**arguments)
  sqs = boto3.client("sqs", region_name=p.region)
  try:
    kwargs: dict[str, Any] = {"MaxResults": min(p.max_items, 1000)}
    if p.queue_name_prefix:
      kwargs["QueueNamePrefix"] = p.queue_name_prefix
    resp = sqs.list_queues(**kwargs)
    urls = resp.get("QueueUrls", [])
    if not urls:
      return [TextContent(type="text", text=f"No SQS queues found in region={p.region}.")]
    queues = []
    for url in urls[:p.max_items]:
      try:
        attrs = sqs.get_queue_attributes(
          QueueUrl=url,
          AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed", "QueueArn"]
        ).get("Attributes", {})
        queues.append({"QueueUrl": url, **attrs})
      except Exception:
        queues.append({"QueueUrl": url})
    import json
    return [TextContent(type="text", text=f"Found {len(queues)} SQS queue(s) in region={p.region}:\n{json.dumps(queues)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error listing SQS queues: {exc}")]


async def _get_sqs_queue_attributes(arguments: dict[str, Any]) -> list[TextContent]:
  p = GetSQSQueueAttributesInput(**arguments)
  sqs = boto3.client("sqs", region_name=p.region)
  try:
    attrs = sqs.get_queue_attributes(QueueUrl=p.queue_url, AttributeNames=["All"]).get("Attributes", {})
    import json
    return [TextContent(type="text", text=f"SQS queue attributes for {p.queue_url}:\n{json.dumps(attrs, default=str)}")]
  except (ClientError, BotoCoreError) as exc:
    return [TextContent(type="text", text=f"Error getting SQS queue attributes: {exc}")]


async def handle_tool_call(name: str, arguments: dict[str, Any]) -> list[TextContent]:
  if name == "list_sns_topics": return await _list_sns_topics(arguments)
  if name == "get_sns_topic_attributes": return await _get_sns_topic_attributes(arguments)
  if name == "list_sqs_queues": return await _list_sqs_queues(arguments)
  if name == "get_sqs_queue_attributes": return await _get_sqs_queue_attributes(arguments)
  return [TextContent(type="text", text=f"Unknown tool: {name}")]
