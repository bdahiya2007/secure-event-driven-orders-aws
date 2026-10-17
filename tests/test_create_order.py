import json
import unittest

from _stubs import BotoCoreError, ClientError, TransactionCanceledException, load_handler

HANDLER = "terraform/modules/create_order/src/handler.py"
ENV = {"EVENT_BUS_NAME": "orders-test", "TABLE_NAME": "Orders-test"}

ORDER_ID = "0b3f2c1e-5a4d-4c6b-9e7a-1f2d3c4b5a69"
RECEIVED_AT = 1760000000000  # 2025-10-09T08:53:20+00:00


def message(**overrides):
    body = {
        "orderId": ORDER_ID,
        "receivedAt": RECEIVED_AT,
        "customerId": "cust-1",
        "items": [{"itemId": "item-1", "quantity": 2}],
    }
    body.update(overrides)
    return body


def record(message_id, body):
    return {"messageId": message_id, "body": body if isinstance(body, str) else json.dumps(body)}


def stored_line(item_id, quantity, customer="cust-1"):
    return {"itemId": {"S": item_id}, "quantity": {"N": str(quantity)}, "customerId": {"S": customer}}


class CreateOrderTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler(HANDLER, "create_order_handler", ENV)
        self.calls = []
        self.handler.dynamodb.transact_write_items.side_effect = lambda **kw: self.calls.append(("save", kw))
        self.handler.events.put_events.side_effect = lambda **kw: (
            self.calls.append(("publish", kw)) or {"FailedEntryCount": 0, "Entries": [{}]}
        )

    def run_batch(self, *records):
        return self.handler.lambda_handler({"Records": list(records)}, None)

    def failed_ids(self, result):
        return [failure["itemIdentifier"] for failure in result["batchItemFailures"]]

    def assert_nothing_written(self):
        self.assertEqual(self.calls, [])

    # --- validation: a rejected message writes nothing and is reported for retry and dead-lettering ---

    def test_malformed_messages_are_reported_and_write_nothing(self):
        for name, body in {"not-json": "{not json", "list": "[1]", "text": '"text"', "null": "null"}.items():
            with self.subTest(name=name):
                self.assertEqual(self.failed_ids(self.run_batch(record(name, body))), [name])
        self.assert_nothing_written()

    def test_order_id_must_be_a_lowercase_uuid(self):
        for order_id in (None, "", "not-a-uuid", 5, ORDER_ID.upper(), ORDER_ID.replace("-", ""), "x" * 36):
            with self.subTest(order_id=order_id):
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message(orderId=order_id)))), ["m"])
        self.assert_nothing_written()

    def test_received_at_must_be_a_millisecond_timestamp(self):
        for value in (None, 0, -1, "1760000000000", 1.5, True, 99999999999999):
            with self.subTest(value=value):
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message(receivedAt=value)))), ["m"])
        self.assert_nothing_written()

    def test_missing_or_bad_customer_id_is_rejected(self):
        for customer in (None, "", 7, "x" * 65):
            with self.subTest(customer=customer):
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message(customerId=customer)))), ["m"])
        self.assert_nothing_written()

    def test_items_must_be_a_list_of_one_to_one_hundred(self):
        one = {"itemId": "i", "quantity": 1}
        for items in (None, [], "items", {"itemId": "i"}, [{"itemId": f"i{n}", "quantity": 1} for n in range(101)]):
            with self.subTest(items=str(items)[:30]):
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message(items=items)))), ["m"])
        self.assert_nothing_written()
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message(items=[one])))), [])

    def test_each_item_must_be_an_object_with_a_valid_unique_item_id(self):
        bad = ("i1", None, {"quantity": 1}, {"itemId": ""}, {"itemId": 5}, {"itemId": "x" * 65})
        for item in bad:
            with self.subTest(item=item):
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message(items=[item])))), ["m"])
        twice = [{"itemId": "a", "quantity": 1}, {"itemId": "a", "quantity": 2}]
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message(items=twice)))), ["m"])
        self.assert_nothing_written()

    def test_quantity_must_be_an_integer_from_1_to_1000(self):
        for quantity in (0, -1, 1001, 1.5, "2", True, None):
            with self.subTest(quantity=quantity):
                items = [{"itemId": "i", "quantity": quantity}]
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message(items=items)))), ["m"])
        self.assert_nothing_written()

    def test_quantity_defaults_to_one_and_accepts_the_limits(self):
        for quantity in (1, 1000):
            with self.subTest(quantity=quantity):
                items = [{"itemId": "i", "quantity": quantity}]
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message(items=items)))), [])
        self.calls.clear()
        self.run_batch(record("m", message(items=[{"itemId": "i"}])))
        item = dict(self.calls)["save"]["TransactItems"][0]["Put"]["Item"]
        self.assertEqual(item["quantity"], {"N": "1"})

    # --- the happy path ---

    def test_valid_message_is_saved_then_published(self):
        self.assertEqual(self.run_batch(record("m1", message())), {"batchItemFailures": []})
        self.assertEqual([name for name, _ in self.calls], ["save", "publish"])

    def test_one_item_is_saved_per_order_line_with_the_message_values(self):
        items = [{"itemId": "a", "quantity": 1}, {"itemId": "b", "quantity": 3}]
        self.run_batch(record("m", message(items=items)))
        save = dict(self.calls)["save"]["TransactItems"]
        self.assertEqual([put["Put"]["Item"]["itemId"]["S"] for put in save], ["a", "b"])
        for put in save:
            item = put["Put"]["Item"]
            self.assertEqual(put["Put"]["TableName"], "Orders-test")
            self.assertEqual(put["Put"]["ConditionExpression"], "attribute_not_exists(orderId)")
            self.assertEqual(item["orderId"]["S"], ORDER_ID)
            self.assertEqual(item["customerId"]["S"], "cust-1")
            self.assertEqual(item["createdAt"]["S"], "2025-10-09T08:53:20+00:00")

    def test_unknown_fields_are_dropped(self):
        hostile = message(items=[{"itemId": "a", "quantity": 1, "price": 0, "admin": True}], role="admin")
        self.run_batch(record("m", hostile))
        item = dict(self.calls)["save"]["TransactItems"][0]["Put"]["Item"]
        self.assertEqual(set(item), {"orderId", "itemId", "quantity", "customerId", "createdAt"})
        detail = json.loads(dict(self.calls)["publish"]["Entries"][0]["Detail"])
        self.assertEqual(set(detail), {"orderId", "customerId", "items", "createdAt"})
        self.assertEqual(detail["items"], [{"itemId": "a", "quantity": 1}])

    def test_published_event_has_the_expected_shape(self):
        self.run_batch(record("m", message()))
        entry = dict(self.calls)["publish"]["Entries"][0]
        self.assertEqual(entry["EventBusName"], "orders-test")
        self.assertEqual(entry["Source"], "orders.create-order")
        self.assertEqual(entry["DetailType"], "OrderCreated")
        self.assertEqual(
            json.loads(entry["Detail"]),
            {
                "orderId": ORDER_ID,
                "customerId": "cust-1",
                "items": [{"itemId": "item-1", "quantity": 2}],
                "createdAt": "2025-10-09T08:53:20+00:00",
            },
        )

    # --- failures: a failed save never publishes; a failed publish is retried and never loses the order ---

    def test_only_failed_messages_in_a_batch_are_reported(self):
        result = self.run_batch(record("good-1", message()), record("bad", "nope"), record("good-2", message()))
        self.assertEqual(self.failed_ids(result), ["bad"])
        self.assertEqual([name for name, _ in self.calls].count("publish"), 2)

    def test_a_dynamodb_error_is_reported_and_publishes_nothing(self):
        error = ClientError({"Error": {"Code": "InternalServerError", "Message": "secret detail"}}, "TransactWriteItems")
        self.handler.dynamodb.transact_write_items.side_effect = error
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.handler.events.put_events.assert_not_called()

    def test_a_cancelled_transaction_for_another_reason_is_retried(self):
        self.handler.dynamodb.transact_write_items.side_effect = TransactionCanceledException(["TransactionConflict"])
        # Even if a matching order happened to be stored, only a failed condition counts as "already saved".
        self.handler.dynamodb.query.return_value = {"Items": [stored_line("item-1", 2)]}
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.handler.events.put_events.assert_not_called()

    def test_a_failed_publish_entry_is_reported_for_retry(self):
        self.handler.events.put_events.side_effect = None
        self.handler.events.put_events.return_value = {
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "boom"}],
        }
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])

    def test_a_publish_exception_is_reported_for_retry(self):
        self.handler.events.put_events.side_effect = BotoCoreError("network down")
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])

    # --- redelivery: the same message may arrive again after a partial failure ---

    def test_a_redelivered_message_for_a_saved_order_is_published_not_saved_twice(self):
        self.handler.dynamodb.transact_write_items.side_effect = TransactionCanceledException(["ConditionalCheckFailed"])
        self.handler.dynamodb.query.return_value = {"Items": [stored_line("item-1", 2)]}
        self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})
        self.handler.events.put_events.assert_called_once()
        self.assertTrue(self.handler.dynamodb.query.call_args.kwargs["ConsistentRead"])

    def test_a_save_then_failed_publish_then_retry_ends_published_exactly_once_more(self):
        outcomes = iter([BotoCoreError("down"), {"FailedEntryCount": 0, "Entries": [{}]}])

        def publish(**kw):
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

        self.handler.events.put_events.side_effect = publish
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])  # saved, publish failed
        self.handler.dynamodb.transact_write_items.side_effect = TransactionCanceledException(["ConditionalCheckFailed"])
        self.handler.dynamodb.query.return_value = {"Items": [stored_line("item-1", 2)]}
        self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})  # retry publishes
        self.assertEqual(self.handler.events.put_events.call_count, 2)

    def test_a_message_reusing_an_existing_order_id_with_different_content_is_rejected(self):
        self.handler.dynamodb.transact_write_items.side_effect = TransactionCanceledException(["ConditionalCheckFailed"])
        for stored in (
            [stored_line("item-1", 5)],  # different quantity
            [stored_line("other", 2)],  # different item
            [stored_line("item-1", 2, "someone-else")],  # different customer
            [],  # nothing stored
        ):
            with self.subTest(stored=str(stored)[:40]):
                self.handler.dynamodb.query.return_value = {"Items": stored}
                self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.handler.events.put_events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
