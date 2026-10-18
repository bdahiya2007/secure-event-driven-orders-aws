import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config
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
PROCESSING_TABLE_NAME = os.environ["PROCESSING_TABLE_NAME"]

MAX_ITEMS = 100  # DynamoDB transaction limit
MAX_ID_LENGTH = 64
MAX_QUANTITY = 1000

# Idempotency: one record per orderId in the processing table.
#   IN_PROGRESS  an invocation holds a lease on the order (lockOwner, lockedUntil)
#   COMPLETED    the order is saved and its event published; `result` holds the outcome
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
LEASE_SECONDS = 60  # longer than the function timeout; a crashed invocation's lease simply runs out
RECORD_TTL_SECONDS = 7 * 24 * 3600  # longer than the 4-day queue retention, so a late redelivery is still recognised


class ValidationError(Exception):
    pass


class InProgress(Exception):
    """Another invocation holds a valid lease on this order. The message is retried after the visibility timeout."""


def _now():
    return int(time.time())


def _identifier(value, name):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_ID_LENGTH:
        raise ValidationError(f"{name} must be a string of 1-{MAX_ID_LENGTH} characters")
    return value


def _parse(record):
    """Validate one queue message and return a clean order.

    API Gateway already validated the request and built this message, but nothing is trusted: every
    field is checked again and only allow-listed fields are kept, so anything else on the queue cannot
    store arbitrary data or override a server-set value.
    """
    try:
        message = json.loads(record["body"])
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ValidationError("Message is not valid JSON")
    if not isinstance(message, dict):
        raise ValidationError("Message must be an object")

    try:
        order_id = str(uuid.UUID(str(message.get("orderId"))))
    except ValueError:
        raise ValidationError("orderId must be a UUID")
    if order_id != message["orderId"]:
        raise ValidationError("orderId must be a lowercase UUID")

    received_at = message.get("receivedAt")
    if isinstance(received_at, bool) or not isinstance(received_at, int) or not 0 < received_at < 4102444800000:
        raise ValidationError("receivedAt must be a millisecond timestamp")

    customer_id = _identifier(message.get("customerId"), "customerId")

    items = message.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= MAX_ITEMS:
        raise ValidationError(f"items must be a list of 1-{MAX_ITEMS} entries")

    lines = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("Each item must be an object")
        quantity = item.get("quantity", 1)
        # bool is a subclass of int, so reject it explicitly.
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 < quantity <= MAX_QUANTITY:
            raise ValidationError(f"quantity must be an integer from 1 to {MAX_QUANTITY}")
        item_id = _identifier(item.get("itemId"), "itemId")
        if item_id in seen:
            raise ValidationError("Duplicate itemId")
        seen.add(item_id)
        lines.append({"itemId": item_id, "quantity": quantity})

    # createdAt comes from when API Gateway received the request, so it is the same on every retry.
    created_at = datetime.fromtimestamp(received_at / 1000, timezone.utc).isoformat()
    return {"orderId": order_id, "customerId": customer_id, "items": lines, "createdAt": created_at}


def _payload_hash(order):
    """Fingerprint of what the order says, so an orderId reused for different content is detected."""
    canonical = {
        "customerId": order["customerId"],
        "items": sorted(order["items"], key=lambda line: line["itemId"]),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_record(order_id):
    page = dynamodb.query(
        TableName=PROCESSING_TABLE_NAME,
        KeyConditionExpression="orderId = :o",
        ExpressionAttributeValues={":o": {"S": order_id}},
        ConsistentRead=True,
    )
    items = page["Items"]
    return items[0] if items else None


def _claim(order, owner, now):
    """Take the lease on this orderId with one conditional write, so concurrent deliveries cannot both win.

    The write succeeds only if there is no record yet, or the previous holder's lease has run out and it
    was for the same content. Returns None when this invocation now holds the lease, or the existing result
    when the order was already completed. Raises InProgress if another invocation holds a valid lease.
    """
    digest = _payload_hash(order)
    try:
        dynamodb.put_item(
            TableName=PROCESSING_TABLE_NAME,
            Item={
                "orderId": {"S": order["orderId"]},
                "status": {"S": IN_PROGRESS},
                "lockOwner": {"S": owner},
                "lockedUntil": {"N": str(now + LEASE_SECONDS)},
                "payloadHash": {"S": digest},
                "createdAt": {"S": order["createdAt"]},
                "updatedAt": {"N": str(now)},
                "expiresAt": {"N": str(now + RECORD_TTL_SECONDS)},
            },
            ConditionExpression=(
                "attribute_not_exists(orderId) OR "
                "(#s = :in_progress AND lockedUntil < :now AND payloadHash = :hash)"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":in_progress": {"S": IN_PROGRESS},
                ":now": {"N": str(now)},
                ":hash": {"S": digest},
            },
        )
        return None
    except dynamodb.exceptions.ConditionalCheckFailedException:
        pass

    record = _read_record(order["orderId"])
    if record is None:
        raise InProgress("The processing record changed; retry")
    if record["payloadHash"]["S"] != digest:
        raise ValidationError("This orderId was already used for a different order")
    if record["status"]["S"] == COMPLETED:
        return json.loads(record["result"]["S"])
    raise InProgress("Another invocation is processing this order")


def _save(order):
    """Save one DynamoDB item per order line, atomically. Returns True if it was newly saved."""
    try:
        dynamodb.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": TABLE_NAME,
                        "Item": {
                            k: serializer.serialize(v)
                            for k, v in {
                                "orderId": order["orderId"],
                                "itemId": line["itemId"],
                                "quantity": line["quantity"],
                                "customerId": order["customerId"],
                                "createdAt": order["createdAt"],
                            }.items()
                        },
                        "ConditionExpression": "attribute_not_exists(orderId)",
                    }
                }
                for line in order["items"]
            ]
        )
        return True
    except dynamodb.exceptions.TransactionCanceledException as e:
        reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        # Only "condition failed" means the lines already exist (an earlier attempt saved this order).
        # Anything else, such as throttling or a conflict, is retried.
        if "ConditionalCheckFailed" in reasons and set(reasons) <= {"ConditionalCheckFailed", "None"}:
            return False
        logger.warning("Order save cancelled: %s", reasons)
        raise


def _confirm_already_saved(order):
    """The lines already exist from an earlier attempt. Make sure the stored order really is this one."""
    stored = {}
    customers = set()
    request = {
        "TableName": TABLE_NAME,
        "KeyConditionExpression": "orderId = :o",
        "ExpressionAttributeValues": {":o": {"S": order["orderId"]}},
        "ProjectionExpression": "#i, #q, #c",
        "ExpressionAttributeNames": {"#i": "itemId", "#q": "quantity", "#c": "customerId"},
        "ConsistentRead": True,
    }
    while True:
        page = dynamodb.query(**request)
        for item in page["Items"]:
            stored[item["itemId"]["S"]] = int(item["quantity"]["N"])
            customers.add(item["customerId"]["S"])
        if "LastEvaluatedKey" not in page:
            break
        request["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    wanted = {line["itemId"]: line["quantity"] for line in order["items"]}
    if stored != wanted or customers != {order["customerId"]}:
        raise ValidationError("A different order with this orderId is already stored")


def _publish(order):
    result = events.put_events(
        Entries=[
            {
                "EventBusName": EVENT_BUS_NAME,
                "Source": "orders.create-order",
                "DetailType": "OrderCreated",
                "Detail": json.dumps(order),
            }
        ]
    )
    if result["FailedEntryCount"] > 0:
        entry = result["Entries"][0]
        raise RuntimeError(f"PutEvents failed: {entry.get('ErrorCode')} {entry.get('ErrorMessage')}")


def _complete(order, owner, result, now):
    """Record the outcome. Only the invocation that still holds the lease may do so."""
    try:
        dynamodb.put_item(
            TableName=PROCESSING_TABLE_NAME,
            Item={
                "orderId": {"S": order["orderId"]},
                "status": {"S": COMPLETED},
                "payloadHash": {"S": _payload_hash(order)},
                "result": {"S": json.dumps(result)},
                "createdAt": {"S": order["createdAt"]},
                "updatedAt": {"N": str(now)},
                "expiresAt": {"N": str(now + RECORD_TTL_SECONDS)},
            },
            ConditionExpression="#s = :in_progress AND lockOwner = :owner",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":in_progress": {"S": IN_PROGRESS}, ":owner": {"S": owner}},
        )
    except dynamodb.exceptions.ConditionalCheckFailedException:
        # The lease ran out and another invocation took the order over. The work is done either way,
        # and that invocation will record the same result.
        logger.warning("Lease lost before the result was recorded for order %s", order["orderId"])


def process_order(order, owner):
    """Create the order exactly once in effect. Returns (result, already_processed)."""
    existing = _claim(order, owner, _now())
    if existing is not None:
        return existing, True

    if not _save(order):
        _confirm_already_saved(order)
    # Published after the order is saved. A failure from here on returns the message to the queue; the retry
    # takes over the expired lease, finds the order saved, and publishes again.
    _publish(order)

    result = {
        "orderId": order["orderId"],
        "status": "CREATED",
        "createdAt": order["createdAt"],
        "lines": len(order["items"]),
    }
    _complete(order, owner, result, _now())
    return result, False


def lambda_handler(event, context):
    failures = []
    request_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())

    for record in event["Records"]:
        try:
            order = _parse(record)
            owner = f"{request_id}:{record['messageId']}"
            result, already = process_order(order, owner)
            if already:
                logger.info("Order already processed, nothing repeated: %s (%s)", result["orderId"], result["status"])
            else:
                logger.info("Order created: %s", result["orderId"])
        except Exception:
            # Log the message id only, never the body. After maxReceiveCount attempts SQS moves the
            # message to the dead-letter queue.
            logger.exception("Failed to process message %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})

    return {"batchItemFailures": failures}
