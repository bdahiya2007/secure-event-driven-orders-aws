import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.types import TypeSerializer

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Bounded retries and timeouts so a slow dependency cannot hold the function open.
_config = Config(
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=3,
    read_timeout=5,
)
dynamodb = boto3.client("dynamodb", config=_config)
events = boto3.client("events", config=_config)
serializer = TypeSerializer()

EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]
TABLE_NAME = os.environ["TABLE_NAME"]

MAX_BODY_BYTES = 256 * 1024
MAX_ITEMS = 100  # DynamoDB transaction limit
MAX_ID_LENGTH = 64
MAX_QUANTITY = 1000


class ValidationError(Exception):
    pass


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse(event):
    # Supports API Gateway proxy events (JSON string body) and direct invocation.
    body = event.get("body", event) if isinstance(event, dict) else None
    if body is None:
        raise ValidationError("Request must be a JSON object")
    if not isinstance(body, str):
        body = json.dumps(body)
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValidationError("Request body is too large")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ValidationError("Body must be valid JSON")


def _identifier(value, name):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_ID_LENGTH:
        raise ValidationError(f"{name} must be a string of 1-{MAX_ID_LENGTH} characters")
    return value


def _validate(order):
    """Return a clean order built only from allow-listed, type-checked fields.

    Unknown client fields are dropped, so callers cannot write arbitrary
    attributes or override server-set values such as orderId and createdAt.
    """
    if not isinstance(order, dict):
        raise ValidationError("Request must be a JSON object")

    customer_id = _identifier(order.get("customerId"), "customerId")

    items = order.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= MAX_ITEMS:
        raise ValidationError(f"items must be a list of 1-{MAX_ITEMS} entries")

    clean_items = []
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("Each item must be an object")
        quantity = item.get("quantity", 1)
        # bool is a subclass of int, so reject it explicitly.
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 < quantity <= MAX_QUANTITY:
            raise ValidationError(f"quantity must be an integer from 1 to {MAX_QUANTITY}")
        clean_items.append({"itemId": _identifier(item.get("itemId"), "itemId"), "quantity": quantity})

    return {"customerId": customer_id, "items": clean_items}


def lambda_handler(event, context):
    try:
        order = _validate(_parse(event))
    except ValidationError as e:
        return _response(400, {"message": str(e)})

    order_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    # One DynamoDB item per order line (orderId + itemId), written atomically.
    try:
        dynamodb.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": TABLE_NAME,
                        "Item": {
                            k: serializer.serialize(v)
                            for k, v in {
                                "orderId": order_id,
                                "itemId": item["itemId"],
                                "quantity": item["quantity"],
                                "customerId": order["customerId"],
                                "createdAt": created_at,
                            }.items()
                        },
                        "ConditionExpression": "attribute_not_exists(orderId)",
                    }
                }
                for item in order["items"]
            ]
        )
    except dynamodb.exceptions.TransactionCanceledException as e:
        # Log only the cancellation reason codes, never request data.
        reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        logger.warning("Order save cancelled: %s", reasons)
        return _response(400, {"message": "Order could not be saved; check for duplicate itemId"})
    except (ClientError, BotoCoreError):
        logger.exception("Order save failed")
        return _response(500, {"message": "Internal error"})

    # Published only after the order is saved, so a failed save never emits an event.
    detail = {
        "orderId": order_id,
        "customerId": order["customerId"],
        "items": order["items"],
        "createdAt": created_at,
    }

    try:
        result = events.put_events(
            Entries=[
                {
                    "EventBusName": EVENT_BUS_NAME,
                    "Source": "orders.create-order",
                    "DetailType": "OrderCreated",
                    "Detail": json.dumps(detail),
                }
            ]
        )
    except (ClientError, BotoCoreError):
        logger.exception("Event publish failed")
        return _response(500, {"message": "Order saved but event publish failed", "orderId": order_id})

    if result["FailedEntryCount"] > 0:
        entry = result["Entries"][0]
        logger.error("PutEvents failed: %s %s", entry.get("ErrorCode"), entry.get("ErrorMessage"))
        return _response(500, {"message": "Order saved but event publish failed", "orderId": order_id})

    logger.info("Order created: %s", order_id)
    return _response(201, {"orderId": order_id})
