import json
import unittest

from _stubs import ConditionalCheckFailedException, load_handler

HANDLER = "terraform/modules/fulfil_order/src/handler.py"
ENV = {"TABLE_NAME": "Fulfilments-test", "ORDERS_TABLE_NAME": "Orders-test"}


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


def stored_line(item_id, quantity, customer="cust-1"):
    return {"itemId": {"S": item_id}, "quantity": {"N": str(quantity)}, "customerId": {"S": customer}}


def record(message_id, body):
    return {"messageId": message_id, "body": body if isinstance(body, str) else json.dumps(body)}


class FulfilOrderTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler(HANDLER, "fulfil_order_handler", ENV)
        self.puts = []
        self.queries = []
        self.handler.dynamodb.put_item.side_effect = lambda **kw: self.puts.append(kw)
        # By default the Orders table holds exactly the order that event() describes.
        self.stored = [stored_line("a", 2), stored_line("b", 3)]
        self.handler.dynamodb.query.side_effect = self.fake_query

    def fake_query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": list(self.stored)}

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


    # --- the event must describe an order that really exists ---

    def test_the_stored_order_is_read_with_a_consistent_read_of_the_orders_table(self):
        self.run_batch(record("m1", event()))
        self.assertEqual(len(self.queries), 1)
        query = self.queries[0]
        self.assertEqual(query["TableName"], "Orders-test")
        self.assertEqual(query["ExpressionAttributeValues"], {":o": {"S": "order-1"}})
        self.assertTrue(query["ConsistentRead"])

    def test_an_event_for_an_order_that_does_not_exist_is_rejected(self):
        self.stored = []
        result = self.run_batch(record("forged", event()))
        self.assertEqual(self.failed_ids(result), ["forged"])
        self.assertEqual(self.puts, [])

    def test_an_event_for_a_different_customer_is_rejected(self):
        self.stored = [stored_line("a", 2, "someone-else"), stored_line("b", 3, "someone-else")]
        self.assertEqual(self.failed_ids(self.run_batch(record("m", event()))), ["m"])
        self.assertEqual(self.puts, [])

    def test_an_event_with_different_quantities_is_rejected(self):
        self.stored = [stored_line("a", 2), stored_line("b", 4)]
        self.assertEqual(self.failed_ids(self.run_batch(record("m", event()))), ["m"])
        self.assertEqual(self.puts, [])

    def test_an_event_with_extra_or_missing_lines_is_rejected(self):
        for stored in ([stored_line("a", 2)], [stored_line("a", 2), stored_line("b", 3), stored_line("c", 1)]):
            with self.subTest(lines=len(stored)):
                self.stored = stored
                self.assertEqual(self.failed_ids(self.run_batch(record("m", event()))), ["m"])
        self.assertEqual(self.puts, [])

    def test_duplicate_item_ids_in_an_event_are_rejected(self):
        detail = event()["detail"]
        duplicated = event(detail={**detail, "items": [{"itemId": "a", "quantity": 1}, {"itemId": "a", "quantity": 1}]})
        self.assertEqual(self.failed_ids(self.run_batch(record("m", duplicated))), ["m"])
        self.assertEqual(self.queries, [])

    def test_a_paged_query_result_is_read_to_the_end(self):
        pages = [
            {"Items": [stored_line("a", 2)], "LastEvaluatedKey": {"orderId": {"S": "order-1"}}},
            {"Items": [stored_line("b", 3)]},
        ]
        self.handler.dynamodb.query.side_effect = lambda **kw: self.queries.append(kw) or pages[len(self.queries) - 1]
        self.assertEqual(self.run_batch(record("m", event())), {"batchItemFailures": []})
        self.assertEqual(len(self.queries), 2)
        self.assertEqual(self.queries[1]["ExclusiveStartKey"], {"orderId": {"S": "order-1"}})
        self.assertEqual(len(self.puts), 1)

    def test_a_failed_lookup_is_retried_not_recorded(self):
        self.handler.dynamodb.query.side_effect = RuntimeError("throttled")
        self.assertEqual(self.failed_ids(self.run_batch(record("m", event()))), ["m"])
        self.assertEqual(self.puts, [])

    def test_a_forged_message_does_not_stop_genuine_ones_in_the_same_batch(self):
        genuine = event()
        forged = event(detail={**genuine["detail"], "orderId": "order-forged"})

        def lookup(**kwargs):
            if kwargs["ExpressionAttributeValues"][":o"]["S"] == "order-forged":
                return {"Items": []}
            return {"Items": list(self.stored)}

        self.handler.dynamodb.query.side_effect = lookup
        result = self.run_batch(record("good", genuine), record("fake", forged))
        self.assertEqual(self.failed_ids(result), ["fake"])
        self.assertEqual([call["Item"]["orderId"]["S"] for call in self.puts], ["order-1"])


if __name__ == "__main__":
    unittest.main()
