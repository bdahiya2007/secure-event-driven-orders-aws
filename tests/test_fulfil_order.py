import json
import unittest

from _stubs import ConditionalCheckFailedException, load_handler

HANDLER = "terraform/modules/fulfil_order/src/handler.py"
ENV = {"TABLE_NAME": "Fulfilments-test"}


def event(**overrides):
    body = {
        "id": "evt-1",
        "detail-type": "OrderCreated",
        "detail": {
            "orderId": "order-1",
            "customerId": "cust-1",
            "createdAt": "2026-10-17T00:00:00+00:00",
            "items": [{"itemId": "a", "quantity": 2}, {"itemId": "b", "quantity": 3}],
        },
    }
    body.update(overrides)
    return body


def record(message_id, body):
    return {"messageId": message_id, "body": body if isinstance(body, str) else json.dumps(body)}


class FulfilOrderTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler(HANDLER, "fulfil_order_handler", ENV)
        self.puts = []
        self.handler.dynamodb.put_item.side_effect = lambda **kw: self.puts.append(kw)

    def run_batch(self, *records):
        return self.handler.lambda_handler({"Records": list(records)}, None)

    def failed_ids(self, result):
        return [failure["itemIdentifier"] for failure in result["batchItemFailures"]]

    def test_valid_message_is_recorded_as_fulfilled_with_totals(self):
        result = self.run_batch(record("m1", event()))
        self.assertEqual(result, {"batchItemFailures": []})
        self.assertEqual(len(self.puts), 1)
        call = self.puts[0]
        self.assertEqual(call["TableName"], "Fulfilments-test")
        self.assertEqual(call["ConditionExpression"], "attribute_not_exists(orderId)")
        item = call["Item"]
        self.assertEqual(item["orderId"], {"S": "order-1"})
        self.assertEqual(item["status"], {"S": "FULFILLED"})
        self.assertEqual(item["itemCount"], {"N": "2"})
        self.assertEqual(item["totalQuantity"], {"N": "5"})
        self.assertEqual(item["eventId"], {"S": "evt-1"})
        self.assertIn("fulfilledAt", item)

    def test_redelivered_message_is_a_successful_no_op(self):
        self.handler.dynamodb.put_item.side_effect = ConditionalCheckFailedException()
        self.assertEqual(self.run_batch(record("m1", event())), {"batchItemFailures": []})

    def test_unparseable_or_wrong_messages_are_reported_as_failures(self):
        bad_messages = {
            "not-json": "not json at all",
            "no-detail": {"id": "e", "detail-type": "OrderCreated"},
            "wrong-type": event(**{"detail-type": "SomethingElse"}),
            "detail-not-object": event(detail="text"),
            "no-event-id": {k: v for k, v in event().items() if k != "id"},
        }
        for name, body in bad_messages.items():
            with self.subTest(name=name):
                self.puts.clear()
                result = self.run_batch(record(name, body))
                self.assertEqual(self.failed_ids(result), [name])
                self.assertEqual(self.puts, [])

    def test_invalid_items_are_reported_as_failures(self):
        detail = event()["detail"]
        bad_items = ([], "items", [{"quantity": "x"}], [{"quantity": True}], [{"quantity": 0}],
                     [{"quantity": 1001}], [None], [{"quantity": 1}] * 101)
        for items in bad_items:
            with self.subTest(items=str(items)[:30]):
                result = self.run_batch(record("m", event(detail={**detail, "items": items})))
                self.assertEqual(self.failed_ids(result), ["m"])
        self.assertEqual(self.puts, [])

    def test_invalid_identifiers_are_reported_as_failures(self):
        detail = event()["detail"]
        for field in ("orderId", "customerId", "createdAt"):
            for value in (None, "", 5, "x" * 65):
                with self.subTest(field=field, value=value):
                    result = self.run_batch(record("m", event(detail={**detail, field: value})))
                    self.assertEqual(self.failed_ids(result), ["m"])
        self.assertEqual(self.puts, [])

    def test_only_failed_messages_in_a_batch_are_reported(self):
        result = self.run_batch(record("good-1", event()), record("bad", "nope"), record("good-2", event()))
        self.assertEqual(self.failed_ids(result), ["bad"])
        self.assertEqual(len(self.puts), 2)

    def test_unexpected_dynamodb_error_is_reported_as_a_failure(self):
        self.handler.dynamodb.put_item.side_effect = RuntimeError("throttled")
        self.assertEqual(self.failed_ids(self.run_batch(record("m1", event()))), ["m1"])

    def test_a_failing_message_does_not_stop_later_ones(self):
        self.handler.dynamodb.put_item.side_effect = [RuntimeError("boom"), None]
        result = self.run_batch(record("first", event()), record("second", event()))
        self.assertEqual(self.failed_ids(result), ["first"])


if __name__ == "__main__":
    unittest.main()
