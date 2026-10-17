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
ORDERS_TABLE_NAME = os.environ["ORDERS_TABLE_NAME"]

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

    lines = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("Each item must be an object")
        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 < quantity <= MAX_QUANTITY:
            raise ValidationError("Each item needs an integer quantity")
        item_id = _identifier(item.get("itemId"), "itemId")
        if item_id in lines:
            raise ValidationError("Duplicate itemId")
        lines[item_id] = quantity

    return {
        "orderId": _identifier(detail.get("orderId"), "orderId"),
        "customerId": _identifier(detail.get("customerId"), "customerId"),
        "eventId": _identifier(event.get("id"), "id"),
        "orderCreatedAt": _identifier(detail.get("createdAt"), "createdAt"),
        "lines": lines,
        "itemCount": len(lines),
        "totalQuantity": sum(lines.values()),
    }


def _stored_order(order_id):
    """Read the order as saved by CreateOrder: its lines and the customer IDs on them."""
    lines, customers = {}, set()
    request = {
        "TableName": ORDERS_TABLE_NAME,
        "KeyConditionExpression": "orderId = :o",
        "ExpressionAttributeValues": {":o": {"S": order_id}},
        "ProjectionExpression": "#i, #q, #c",
        "ExpressionAttributeNames": {"#i": "itemId", "#q": "quantity", "#c": "customerId"},
        # The event is published after the save commits, so a consistent read always sees it.
        "ConsistentRead": True,
    }
    while True:
        page = dynamodb.query(**request)
        for item in page["Items"]:
            lines[item["itemId"]["S"]] = int(item["quantity"]["N"])
            customers.add(item["customerId"]["S"])
        if "LastEvaluatedKey" not in page:
            return lines, customers
        request["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _verify_order(order):
    """Accept the event only if it describes an order that really exists.

    Anyone who can put an event on the bus or a message on the queue can make it look well formed, so
    the event is checked against the stored order: same customer, same lines, same quantities.
    A forged, altered or unknown order fails here and ends up in the dead-letter queue.
    """
    lines, customers = _stored_order(order["orderId"])
    if lines != order["lines"] or customers != {order["customerId"]}:
        raise ValidationError("Event does not match a stored order")


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
            order = _parse(record)
            _verify_order(order)
            _fulfil(order)
        except Exception:
            # Log the message id only, never the body. After maxReceiveCount
            # attempts SQS moves the message to the dead-letter queue.
            logger.exception("Failed to process message %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
