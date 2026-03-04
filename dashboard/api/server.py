"""Argus Monitoring Dashboard — FastAPI Backend.

Proxies AWS SDK calls to provide dashboard data:
- DynamoDB: reviews and findings
- CloudWatch: Lambda metrics
- SQS: queue depths
- Lambda: function status
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Argus Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
STAGE = os.environ.get("STAGE", "dev")
TABLE_NAME = f"argus-{STAGE}-reviews"

# AWS clients
dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(TABLE_NAME)
cloudwatch = boto3.client("cloudwatch", region_name=REGION)
sqs_client = boto3.client("sqs", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
sns_client = boto3.client("sns", region_name=REGION)


def _get_queue_url(name: str) -> str:
    """Get SQS queue URL by name."""
    try:
        resp = sqs_client.get_queue_url(QueueName=f"argus-{STAGE}-{name}")
        return resp["QueueUrl"]
    except Exception:
        return ""


def _get_queue_depth(queue_url: str) -> int:
    """Get approximate number of messages in an SQS queue."""
    if not queue_url:
        return 0
    try:
        resp = sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(resp["Attributes"].get("ApproximateNumberOfMessages", 0))
    except Exception:
        return 0


def _get_lambda_metric(
    fn_name: str, metric: str, stat: str = "Sum", period_hours: int = 24
) -> float:
    """Get a CloudWatch metric for a Lambda function."""
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=period_hours)
        resp = cloudwatch.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName=metric,
            Dimensions=[{"Name": "FunctionName", "Value": fn_name}],
            StartTime=start,
            EndTime=end,
            Period=period_hours * 3600,
            Statistics=[stat],
        )
        datapoints = resp.get("Datapoints", [])
        if datapoints:
            return datapoints[0].get(stat, 0)
        return 0
    except Exception:
        return 0


@app.get("/api/overview")
async def get_overview() -> dict[str, Any]:
    """Aggregated KPIs for the dashboard overview."""
    # Count reviews
    try:
        scan = table.scan(
            FilterExpression="begins_with(sk, :meta)",
            ExpressionAttributeValues={":meta": "META"},
            Select="COUNT",
        )
        total_reviews = scan.get("Count", 0)
    except Exception:
        total_reviews = 0

    # Count findings by severity
    findings_by_severity = {"critical": 0, "warning": 0, "info": 0}
    try:
        scan = table.scan(
            FilterExpression="begins_with(sk, :finding)",
            ExpressionAttributeValues={":finding": "FINDING#"},
        )
        for item in scan.get("Items", []):
            sev = item.get("severity", "info").lower()
            if sev in findings_by_severity:
                findings_by_severity[sev] += 1
    except Exception:
        pass

    # Queue depths
    queues = {}
    for q in [
        "parse-queue", "parse-dlq",
        "security-queue", "security-dlq",
        "style-queue", "style-dlq",
        "performance-queue", "performance-dlq",
        "test-queue", "test-dlq"
    ]:
        url = _get_queue_url(q)
        queues[q] = _get_queue_depth(url)

    # Lambda invocations (last 24h)
    lambda_fns = [
        f"argus-{STAGE}-webhook",
        f"argus-{STAGE}-parser",
        f"argus-{STAGE}-security",
        f"argus-{STAGE}-style",
        f"argus-{STAGE}-performance",
        f"argus-{STAGE}-test"
    ]
    invocations = {}
    errors = {}
    for fn in lambda_fns:
        short = fn.replace(f"argus-{STAGE}-", "")
        invocations[short] = int(_get_lambda_metric(fn, "Invocations"))
        errors[short] = int(_get_lambda_metric(fn, "Errors"))

    return {
        "total_reviews": total_reviews,
        "total_findings": sum(findings_by_severity.values()),
        "findings_by_severity": findings_by_severity,
        "queues": queues,
        "lambda_invocations_24h": invocations,
        "lambda_errors_24h": errors,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/reviews")
async def get_reviews() -> dict[str, Any]:
    """List recent reviews from DynamoDB."""
    try:
        scan = table.scan(
            FilterExpression="begins_with(sk, :meta)",
            ExpressionAttributeValues={":meta": "META"},
            Limit=50,
        )
        items = scan.get("Items", [])

        reviews = []
        for item in items:
            review_id = item.get("pk", "").replace("REV#", "")
            reviews.append({
                "review_id": review_id,
                "repo": item.get("repo_full_name", item.get("repo", "")),
                "pr_number": item.get("pr_number", 0),
                "pr_url": item.get("pr_url", ""),
                "status": item.get("status", "unknown"),
                "timestamp": item.get("timestamp", item.get("created_at", "")),
                "files_count": item.get("files_analyzed", item.get("total_files", 0)),
                "chunks_count": item.get("chunks_count", item.get("total_chunks", 0)),
            })

        reviews.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return {"reviews": reviews, "count": len(reviews)}
    except Exception as e:
        return {"reviews": [], "count": 0, "error": str(e)}


@app.get("/api/findings/{review_id}")
async def get_findings(review_id: str) -> dict[str, Any]:
    """Get findings for a specific review."""
    try:
        scan = table.scan(
            FilterExpression="pk = :pk AND begins_with(sk, :finding)",
            ExpressionAttributeValues={
                ":pk": f"REV#{review_id}",
                ":finding": "FINDING#",
            },
        )
        items = scan.get("Items", [])

        findings = []
        for item in items:
            findings.append({
                "severity": item.get("severity", "info"),
                "category": item.get("category", ""),
                "file": item.get("file", ""),
                "line": item.get("line", 0),
                "message": item.get("message", ""),
                "suggestion": item.get("suggestion", ""),
                "agent": item.get("agent", ""),
            })

        findings.sort(key=lambda f: {"critical": 0, "warning": 1, "info": 2}.get(f["severity"], 3))
        return {"review_id": review_id, "findings": findings, "count": len(findings)}
    except Exception as e:
        return {"review_id": review_id, "findings": [], "count": 0, "error": str(e)}


@app.get("/api/infrastructure")
async def get_infrastructure() -> dict[str, Any]:
    """Infrastructure health: Lambda functions, SQS queues, SNS topics."""
    lambda_fns = [
        f"argus-{STAGE}-webhook",
        f"argus-{STAGE}-parser",
        f"argus-{STAGE}-security",
        f"argus-{STAGE}-style",
        f"argus-{STAGE}-performance",
        f"argus-{STAGE}-test",
    ]

    functions = []
    for fn_name in lambda_fns:
        try:
            config = lambda_client.get_function_configuration(FunctionName=fn_name)
            short = fn_name.replace(f"argus-{STAGE}-", "")
            functions.append({
                "name": short,
                "full_name": fn_name,
                "runtime": config.get("Runtime", ""),
                "memory": config.get("MemorySize", 0),
                "timeout": config.get("Timeout", 0),
                "last_modified": config.get("LastModified", ""),
                "state": config.get("State", "Active"),
                "invocations_24h": int(_get_lambda_metric(fn_name, "Invocations")),
                "errors_24h": int(_get_lambda_metric(fn_name, "Errors")),
                "avg_duration_ms": round(_get_lambda_metric(fn_name, "Duration", "Average"), 1),
            })
        except Exception:
            pass

    # SQS queues
    queues = []
    for q in [
        "parse-queue", "parse-dlq",
        "security-queue", "security-dlq",
        "style-queue", "style-dlq",
        "performance-queue", "performance-dlq",
        "test-queue", "test-dlq"
    ]:
        url = _get_queue_url(q)
        queues.append({
            "name": q,
            "url": url,
            "depth": _get_queue_depth(url),
            "is_dlq": "dlq" in q,
        })

    # SNS topics
    topics = []
    try:
        resp = sns_client.list_topics()
        for topic in resp.get("Topics", []):
            arn = topic["TopicArn"]
            if f"argus-{STAGE}" in arn:
                name = arn.split(":")[-1].replace(f"argus-{STAGE}-", "")
                topics.append({"name": name, "arn": arn})
    except Exception:
        pass

    return {
        "functions": functions,
        "queues": queues,
        "topics": topics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
