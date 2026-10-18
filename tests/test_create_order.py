import json
import sys
import types
import unittest
from unittest import mock

from _stubs import BotoCoreError, ClientError, FakeDynamoDB, load_handler

HANDLER = "terraform/modules/create_order/src/handler.py"
ENV = {"EVENT_BUS_NAME": "orders-test", "TABLE_NAME": "Orders-test", "PROCESSING_TABLE_NAME": "Orders-processing-test"}
ORDERS = "Orders-test"
PROCESSING = "Orders-processing-test"

ORDER_ID = "0b3f2c1e-5a4d-4c6b-9e7a-1f2d3c4b5a69"
RECEIVED_AT = 1760000000000  # 2025-10-09T08:53:20+00:00
NOW = 1760000100  # seconds
LEASE = 60


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


class CreateOrderTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler(HANDLER, "create_order_handler", ENV)
        self.db = FakeDynamoDB()
        self.handler.dynamodb = self.db
        self.published = []
        self.handler.events.put_events.side_effect = self._publish
        self.clock = NOW
        self.handler._now = lambda: self.clock

    def _publish(self, **kwargs):
        self.published.append(kwargs)
        return {"FailedEntryCount": 0, "Entries": [{}]}

    def run_batch(self, *records):
        return self.handler.lambda_handler({"Records": list(records)}, None)

    def failed_ids(self, result):
        return [failure["itemIdentifier"] for failure in result["batchItemFailures"]]

    def processing_record(self, order_id=ORDER_ID):
        return self.db.tables.get(PROCESSING, {}).get((order_id,))

    def saved_lines(self, order_id=ORDER_ID):
        return {k[1] for k in self.db.tables.get(ORDERS, {}) if k[0] == order_id}

    def assert_nothing_written(self):
        self.assertEqual(self.db.tables, {})
        self.assertEqual(self.published, [])

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
        for index, quantity in enumerate((1, 1000)):
            order_id = f"0b3f2c1e-5a4d-4c6b-9e7a-1f2d3c4b5a{index:02d}"
            items = [{"itemId": "i", "quantity": quantity}]
            self.assertEqual(self.failed_ids(self.run_batch(record("m", message(orderId=order_id, items=items)))), [])
        order_id = "0b3f2c1e-5a4d-4c6b-9e7a-1f2d3c4b5a99"
        self.run_batch(record("m", message(orderId=order_id, items=[{"itemId": "i"}])))
        self.assertEqual(self.db.tables[ORDERS][(order_id, "i")]["quantity"], {"N": "1"})

    # --- the happy path ---

    def test_a_new_order_is_claimed_saved_published_and_recorded(self):
        self.assertEqual(self.run_batch(record("m1", message())), {"batchItemFailures": []})
        self.assertEqual(self.saved_lines(), {"item-1"})
        self.assertEqual(len(self.published), 1)
        stored = self.processing_record()
        self.assertEqual(stored["status"]["S"], "COMPLETED")
        self.assertEqual(
            json.loads(stored["result"]["S"]),
            {"orderId": ORDER_ID, "status": "CREATED", "createdAt": "2025-10-09T08:53:20+00:00", "lines": 1},
        )

    def test_the_processing_record_carries_what_a_later_delivery_needs(self):
        self.run_batch(record("m", message()))
        stored = self.processing_record()
        self.assertEqual(stored["expiresAt"], {"N": str(NOW + 7 * 24 * 3600)})  # time-to-live attribute
        self.assertEqual(len(stored["payloadHash"]["S"]), 64)

    def test_one_item_is_saved_per_order_line_with_the_message_values(self):
        items = [{"itemId": "a", "quantity": 1}, {"itemId": "b", "quantity": 3}]
        self.run_batch(record("m", message(items=items)))
        self.assertEqual(self.saved_lines(), {"a", "b"})
        for key, item in self.db.tables[ORDERS].items():
            self.assertEqual(item["orderId"]["S"], ORDER_ID)
            self.assertEqual(item["customerId"]["S"], "cust-1")
            self.assertEqual(item["createdAt"]["S"], "2025-10-09T08:53:20+00:00")

    def test_unknown_fields_are_dropped(self):
        hostile = message(items=[{"itemId": "a", "quantity": 1, "price": 0, "admin": True}], role="admin")
        self.run_batch(record("m", hostile))
        item = self.db.tables[ORDERS][(ORDER_ID, "a")]
        self.assertEqual(set(item), {"orderId", "itemId", "quantity", "customerId", "createdAt"})
        detail = json.loads(self.published[0]["Entries"][0]["Detail"])
        self.assertEqual(set(detail), {"orderId", "customerId", "items", "createdAt"})

    def test_published_event_has_the_expected_shape(self):
        self.run_batch(record("m", message()))
        entry = self.published[0]["Entries"][0]
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

    # --- idempotency: the same orderId again returns the existing result and repeats nothing ---

    def test_a_redelivered_order_returns_the_existing_result_and_repeats_nothing(self):
        order = self.handler._parse(record("m", message()))
        first, first_was_duplicate = self.handler.process_order(order, "owner-1")
        writes_after_first = dict(self.db.counts)
        second, second_was_duplicate = self.handler.process_order(order, "owner-2")
        self.assertFalse(first_was_duplicate)
        self.assertTrue(second_was_duplicate)
        self.assertEqual(second, first)
        self.assertEqual(len(self.published), 1)  # no second event, so no second email or fulfilment
        self.assertEqual(self.db.counts[("transact_write_items", None)], 1)
        self.assertEqual(self.db.counts[("put_item", ORDERS)], writes_after_first.get(("put_item", ORDERS), 0))

    def test_many_deliveries_of_one_message_publish_exactly_once(self):
        for attempt in range(5):
            self.assertEqual(self.run_batch(record(f"m{attempt}", message())), {"batchItemFailures": []})
        self.assertEqual(len(self.published), 1)
        self.assertEqual(len(self.saved_lines()), 1)
        self.assertEqual(self.db.counts[("transact_write_items", None)], 1)

    def test_a_duplicate_in_the_same_batch_is_handled_once(self):
        result = self.run_batch(record("a", message()), record("b", message()))
        self.assertEqual(result, {"batchItemFailures": []})
        self.assertEqual(len(self.published), 1)

    def test_the_same_order_id_with_different_content_is_rejected_and_changes_nothing(self):
        self.run_batch(record("m", message()))
        before_lines = self.saved_lines()
        other = message(items=[{"itemId": "different", "quantity": 9}])
        self.assertEqual(self.failed_ids(self.run_batch(record("again", other))), ["again"])
        self.assertEqual(self.saved_lines(), before_lines)
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.processing_record()["status"]["S"], "COMPLETED")

    def test_lines_already_stored_for_a_different_order_are_never_announced(self):
        # Nothing in the processing table, but the order table already holds other content under this orderId.
        self.db.tables[ORDERS] = {
            (ORDER_ID, "item-1"): {
                "orderId": {"S": ORDER_ID}, "itemId": {"S": "item-1"}, "quantity": {"N": "9"},
                "customerId": {"S": "cust-1"}, "createdAt": {"S": "2025-10-09T08:53:20+00:00"},
            }
        }
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.assertEqual(self.published, [])
        self.assertEqual(self.processing_record()["status"]["S"], "IN_PROGRESS")  # never completed

    def test_item_order_does_not_change_the_fingerprint(self):
        a = message(items=[{"itemId": "a", "quantity": 1}, {"itemId": "b", "quantity": 2}])
        b = message(items=[{"itemId": "b", "quantity": 2}, {"itemId": "a", "quantity": 1}])
        self.run_batch(record("m1", a))
        self.assertEqual(self.run_batch(record("m2", b)), {"batchItemFailures": []})
        self.assertEqual(len(self.published), 1)

    # --- concurrency: only one invocation may process an order at a time ---

    def test_only_one_of_two_simultaneous_claims_wins(self):
        order = self.handler._parse(record("m", message()))
        self.assertIsNone(self.handler._claim(order, "owner-a", NOW))
        with self.assertRaises(self.handler.InProgress):
            self.handler._claim(order, "owner-b", NOW)
        self.assertEqual(self.processing_record()["lockOwner"]["S"], "owner-a")

    def test_a_delivery_that_arrives_while_another_is_processing_does_nothing_and_is_retried(self):
        outcomes = {}

        def publish_while_a_second_delivery_arrives(**kwargs):
            outcomes["second"] = self.run_batch(record("copy", message()))
            return self._publish(**kwargs)

        self.handler.events.put_events.side_effect = publish_while_a_second_delivery_arrives
        self.assertEqual(self.run_batch(record("original", message())), {"batchItemFailures": []})
        self.assertEqual(self.failed_ids(outcomes["second"]), ["copy"])  # told to come back later
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.db.counts[("transact_write_items", None)], 1)
        # when the copy returns it finds the result and repeats nothing
        self.handler.events.put_events.side_effect = self._publish
        self.assertEqual(self.run_batch(record("copy", message())), {"batchItemFailures": []})
        self.assertEqual(len(self.published), 1)

    def test_an_expired_lease_is_taken_over_by_the_next_delivery(self):
        order = self.handler._parse(record("m", message()))
        self.handler._claim(order, "crashed-owner", NOW)
        self.clock = NOW + LEASE + 1
        self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})
        self.assertEqual(self.processing_record()["status"]["S"], "COMPLETED")
        self.assertEqual(len(self.published), 1)

    def test_a_valid_lease_is_not_taken_over(self):
        order = self.handler._parse(record("m", message()))
        self.handler._claim(order, "other-owner", NOW)
        self.clock = NOW + LEASE - 1
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.assertEqual(self.published, [])
        self.assertEqual(self.processing_record()["lockOwner"]["S"], "other-owner")

    def test_an_expired_lease_for_different_content_is_not_taken_over(self):
        other = self.handler._parse(record("m", message(items=[{"itemId": "x", "quantity": 1}])))
        self.handler._claim(other, "crashed-owner", NOW)
        self.clock = NOW + LEASE + 1
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.assertEqual(self.published, [])
        self.assertEqual(self.processing_record()["lockOwner"]["S"], "crashed-owner")

    def test_losing_the_lease_before_the_result_is_recorded_is_not_an_error(self):
        def publish_then_lose_the_lease(**kwargs):
            self.db.tables[PROCESSING][(ORDER_ID,)]["lockOwner"] = {"S": "new-owner"}
            return self._publish(**kwargs)

        self.handler.events.put_events.side_effect = publish_then_lose_the_lease
        self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})
        self.assertEqual(self.processing_record()["status"]["S"], "IN_PROGRESS")  # left for the new owner to finish

    # --- global endpoint: events go through the endpoint when one is configured ---

    def test_without_an_endpoint_the_event_goes_to_the_regional_bus(self):
        self.run_batch(record("m", message()))
        request = self.published[0]
        self.assertNotIn("EndpointId", request)
        self.assertEqual(request["Entries"][0]["EventBusName"], "orders-test")

    def test_with_an_endpoint_the_event_is_published_through_it(self):
        self.handler.EVENT_ENDPOINT_ID = "abc123.xyz"
        with mock.patch.dict(sys.modules, {"awscrt": types.ModuleType("awscrt")}):
            self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})
        request = self.published[0]
        self.assertEqual(request["EndpointId"], "abc123.xyz")
        self.assertEqual(request["Entries"][0]["EventBusName"], "orders-test")  # the bus name, not an ARN
        self.assertEqual(request["Entries"][0]["DetailType"], "OrderCreated")

    def test_an_endpoint_without_the_signing_library_fails_loudly_and_publishes_nothing(self):
        self.handler.EVENT_ENDPOINT_ID = "abc123.xyz"
        with mock.patch.dict(sys.modules, {"awscrt": None}):  # importing it raises ImportError
            self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.assertEqual(self.published, [])
        self.assertEqual(self.processing_record()["status"]["S"], "IN_PROGRESS")  # retried, never marked done

    def test_the_endpoint_id_is_read_from_the_environment(self):
        with_endpoint = load_handler(HANDLER, "create_order_with_endpoint", {**ENV, "EVENT_ENDPOINT_ID": "e1.x"})
        self.assertEqual(with_endpoint.EVENT_ENDPOINT_ID, "e1.x")
        self.assertIsNone(load_handler(HANDLER, "create_order_without_endpoint", {**ENV, "EVENT_ENDPOINT_ID": ""}).EVENT_ENDPOINT_ID)

    # --- failures: a failed step is retried without losing or doubling the order ---

    def test_a_failed_publish_is_retried_and_ends_with_one_event(self):
        self.handler.events.put_events.side_effect = BotoCoreError("network down")
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.assertEqual(self.saved_lines(), {"item-1"})  # saved, not yet announced
        self.assertEqual(self.processing_record()["status"]["S"], "IN_PROGRESS")
        self.handler.events.put_events.side_effect = self._publish
        self.clock = NOW + LEASE + 1  # the lease runs out before SQS redelivers
        self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.processing_record()["status"]["S"], "COMPLETED")
        self.assertEqual(self.db.counts[("transact_write_items", None)], 2)  # second attempt found the lines already stored

    def test_a_failed_publish_entry_is_reported_for_retry(self):
        self.handler.events.put_events.side_effect = None
        self.handler.events.put_events.return_value = {
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "boom"}],
        }
        self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])

    def test_a_save_error_is_reported_and_publishes_nothing(self):
        error = ClientError({"Error": {"Code": "InternalServerError", "Message": "secret detail"}}, "TransactWriteItems")
        with mock.patch.object(self.db, "transact_write_items", side_effect=error):
            self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.assertEqual(self.published, [])

    def test_the_window_between_publish_and_recording_can_repeat_the_event(self):
        # The one case that cannot be exactly once: the event went out, then the result could not be written.
        real_put = self.db.put_item

        def fail_the_completion_write(TableName, Item, **kwargs):
            if TableName == PROCESSING and Item["status"]["S"] == "COMPLETED":
                raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "PutItem")
            return real_put(TableName, Item, **kwargs)

        with mock.patch.object(self.db, "put_item", side_effect=fail_the_completion_write):
            self.assertEqual(self.failed_ids(self.run_batch(record("m", message()))), ["m"])
        self.clock = NOW + LEASE + 1
        self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})
        self.assertEqual(len(self.published), 2)  # at least once; the consumers tolerate repeats
        self.assertEqual(self.run_batch(record("m", message())), {"batchItemFailures": []})
        self.assertEqual(len(self.published), 2)  # and never again once the result is recorded

    def test_only_failed_messages_in_a_batch_are_reported(self):
        second = message(orderId="0b3f2c1e-5a4d-4c6b-9e7a-1f2d3c4b5a70")
        result = self.run_batch(record("good-1", message()), record("bad", "nope"), record("good-2", second))
        self.assertEqual(self.failed_ids(result), ["bad"])
        self.assertEqual(len(self.published), 2)


if __name__ == "__main__":
    unittest.main()
