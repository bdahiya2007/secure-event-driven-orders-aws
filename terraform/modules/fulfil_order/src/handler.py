import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_config = Config(
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=3,
    read_timeout=5,
)
dynamodb = boto3.client("dynamodb", config=_config)

TABLE_NAME = os.environ["TABLE_NAME"]

MAX_ITEMS = 100
MAX_ID_LENGTH = 64
MAX_QUANTITY = 1000


class ValidationError(Exception):
    pass


def _identifier(value, name):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_ID_LENGTH:
        raise ValidationError(f"{name} must be a string of 1-{MAX_ID_LENGTH} characters")
    return value


def _parse(record):
    """Extract and validate the OrderCreated detail from an SQS record.

    The queue body is the full EventBridge event. Nothing in it is trusted:
    every field is re-checked before use.
    """
    try:
        event = json.loads(record["body"])
        detail = event["detail"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ValidationError("Message is not a valid EventBridge event")

    if event.get("detail-type") != "OrderCreated":
        raise ValidationError("Unexpected detail-type")
    if not isinstance(detail, dict):
        raise ValidationError("detail must be an object")

    items = detail.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= MAX_ITEMS:
        raise ValidationError(f"items must be a list of 1-{MAX_ITEMS} entries")

    total_quantity = 0
    for item in items:
        quantity = item.get("quantity") if isinstance(item, dict) else None
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 < quantity <= MAX_QUANTITY:
            raise ValidationError("Each item needs an integer quantity")
        total_quantity += quantity

    return {
        "orderId": _identifier(detail.get("orderId"), "orderId"),
        "customerId": _identifier(detail.get("customerId"), "customerId"),
        "eventId": _identifier(event.get("id"), "id"),
        "orderCreatedAt": _identifier(detail.get("createdAt"), "createdAt"),
        "itemCount": len(items),
        "totalQuantity": total_quantity,
    }


def _fulfil(order):
    """Record the fulfilment. Idempotent: a redelivered message is a no-op."""
    try:
        dynamodb.put_item(
            TableName=TABLE_NAME,
            Item={
                "orderId": {"S": order["orderId"]},
                "customerId": {"S": order["customerId"]},
                "status": {"S": "FULFILLED"},
                "itemCount": {"N": str(order["itemCount"])},
                "totalQuantity": {"N": str(order["totalQuantity"])},
                "eventId": {"S": order["eventId"]},
                "orderCreatedAt": {"S": order["orderCreatedAt"]},
                "fulfilledAt": {"S": datetime.now(timezone.utc).isoformat()},
            },
            ConditionExpression="attribute_not_exists(orderId)",
        )
        logger.info("Order fulfilled: %s", order["orderId"])
    except dynamodb.exceptions.ConditionalCheckFailedException:
        logger.info("Order already fulfilled, skipping: %s", order["orderId"])


def lambda_handler(event, context):
    failures = []

    for record in event["Records"]:
        try:
            _fulfil(_parse(record))
        except Exception:
            # Log the message id only, never the body. After maxReceiveCount
            # attempts SQS moves the message to the dead-letter queue.
            logger.exception("Failed to process message %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
