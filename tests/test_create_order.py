import json
import unittest

from _stubs import BotoCoreError, ClientError, TransactionCanceledException, load_handler

HANDLER = "terraform/modules/create_order/src/handler.py"
ENV = {"EVENT_BUS_NAME": "orders-test", "TABLE_NAME": "Orders-test"}


def order(**overrides):
    body = {"customerId": "cust-1", "items": [{"itemId": "item-1", "quantity": 2}]}
    body.update(overrides)
    return body


class CreateOrderTests(unittest.TestCase):
    def setUp(self):
        self.handler = load_handler(HANDLER, "create_order_handler", ENV)
        self.calls = []
        self.handler.dynamodb.transact_write_items.side_effect = lambda **kw: self.calls.append(("save", kw))
        self.handler.events.put_events.side_effect = lambda **kw: (
            self.calls.append(("publish", kw)) or {"FailedEntryCount": 0, "Entries": [{}]}
        )

    def invoke(self, event):
        response = self.handler.lambda_handler(event, None)
        return response["statusCode"], json.loads(response["body"])

    def assert_nothing_written(self):
        self.assertEqual(self.calls, [])

    # --- validation: every rejection happens before anything is written ---

    def test_invalid_json_body_is_rejected(self):
        status, body = self.invoke({"body": "{not json"})
        self.assertEqual(status, 400)
        self.assertEqual(body["message"], "Body must be valid JSON")
        self.assert_nothing_written()

    def test_non_object_requests_are_rejected(self):
        for event in ("text", ["list"], None, 5):
            with self.subTest(event=event):
                self.assertEqual(self.invoke(event)[0], 400)
        self.assert_nothing_written()

    def test_missing_or_bad_customer_id_is_rejected(self):
        for customer in (None, "", 7, "x" * 65):
            with self.subTest(customer=customer):
                self.assertEqual(self.invoke(order(customerId=customer))[0], 400)
        self.assert_nothing_written()

    def test_items_must_be_a_list_of_one_to_one_hundred(self):
        one = {"itemId": "i", "quantity": 1}
        for items in (None, [], "items", {"itemId": "i"}, [one] * 101):
            with self.subTest(items=str(items)[:30]):
                self.assertEqual(self.invoke(order(items=items))[0], 400)
        self.assert_nothing_written()

    def test_each_item_must_be_an_object_with_a_valid_item_id(self):
        for item in ("i1", None, {"quantity": 1}, {"itemId": ""}, {"itemId": 5}, {"itemId": "x" * 65}):
            with self.subTest(item=item):
                self.assertEqual(self.invoke(order(items=[item]))[0], 400)
        self.assert_nothing_written()

    def test_quantity_must_be_an_integer_from_1_to_1000(self):
        for quantity in (0, -1, 1001, 1.5, "2", True, None):
            with self.subTest(quantity=quantity):
                self.assertEqual(self.invoke(order(items=[{"itemId": "i", "quantity": quantity}]))[0], 400)
        self.assert_nothing_written()

    def test_quantity_defaults_to_one_and_accepts_the_limits(self):
        for quantity in (1, 1000):
            with self.subTest(quantity=quantity):
                self.assertEqual(self.invoke(order(items=[{"itemId": "i", "quantity": quantity}]))[0], 201)
        self.assertEqual(self.invoke(order(items=[{"itemId": "i"}]))[0], 201)

    def test_oversized_body_is_rejected(self):
        status, body = self.invoke({"body": json.dumps(order(customerId="c" * (256 * 1024)))})
        self.assertEqual(status, 400)
        self.assertIn("too large", body["message"])
        self.assert_nothing_written()

    # --- the happy path ---

    def test_valid_order_is_saved_then_published(self):
        status, body = self.invoke(order())
        self.assertEqual(status, 201)
        self.assertEqual([name for name, _ in self.calls], ["save", "publish"])
        self.assertEqual(len(body["orderId"]), 36)

    def test_api_gateway_proxy_body_and_direct_invoke_are_both_accepted(self):
        self.assertEqual(self.invoke({"body": json.dumps(order())})[0], 201)
        self.assertEqual(self.invoke(order())[0], 201)

    def test_one_item_is_saved_per_order_line_with_the_server_set_fields(self):
        status, body = self.invoke(order(items=[{"itemId": "a", "quantity": 1}, {"itemId": "b", "quantity": 3}]))
        save = dict(self.calls)["save"]["TransactItems"]
        self.assertEqual(len(save), 2)
        keys = [put["Put"]["Item"]["itemId"]["S"] for put in save]
        self.assertEqual(keys, ["a", "b"])
        for put in save:
            item = put["Put"]["Item"]
            self.assertEqual(put["Put"]["TableName"], "Orders-test")
            self.assertEqual(put["Put"]["ConditionExpression"], "attribute_not_exists(orderId)")
            self.assertEqual(item["orderId"]["S"], body["orderId"])
            self.assertEqual(item["customerId"]["S"], "cust-1")
            self.assertIn("createdAt", item)

    def test_unknown_client_fields_are_dropped_and_cannot_override_server_values(self):
        hostile = order(items=[{"itemId": "a", "quantity": 1, "orderId": "evil", "price": 0, "admin": True}])
        hostile["orderId"] = "evil"
        status, body = self.invoke(hostile)
        item = dict(self.calls)["save"]["TransactItems"][0]["Put"]["Item"]
        self.assertEqual(set(item), {"orderId", "itemId", "quantity", "customerId", "createdAt"})
        self.assertNotEqual(item["orderId"]["S"], "evil")
        self.assertNotEqual(body["orderId"], "evil")

    def test_published_event_has_the_expected_shape(self):
        status, body = self.invoke(order())
        entry = dict(self.calls)["publish"]["Entries"][0]
        self.assertEqual(entry["EventBusName"], "orders-test")
        self.assertEqual(entry["Source"], "orders.create-order")
        self.assertEqual(entry["DetailType"], "OrderCreated")
        detail = json.loads(entry["Detail"])
        self.assertEqual(set(detail), {"orderId", "customerId", "items", "createdAt"})
        self.assertEqual(detail["orderId"], body["orderId"])
        self.assertEqual(detail["items"], [{"itemId": "item-1", "quantity": 2}])

    # --- failures: a failed save never publishes; a failed publish never loses the order id ---

    def test_cancelled_transaction_returns_400_and_publishes_nothing(self):
        self.handler.dynamodb.transact_write_items.side_effect = TransactionCanceledException(["ConditionalCheckFailed"])
        status, body = self.invoke(order())
        self.assertEqual(status, 400)
        self.assertNotIn("orderId", body)
        self.handler.events.put_events.assert_not_called()

    def test_dynamodb_error_returns_generic_500_and_publishes_nothing(self):
        error = ClientError({"Error": {"Code": "InternalServerError", "Message": "secret detail"}}, "TransactWriteItems")
        self.handler.dynamodb.transact_write_items.side_effect = error
        status, body = self.invoke(order())
        self.assertEqual(status, 500)
        self.assertEqual(body, {"message": "Internal error"})
        self.handler.events.put_events.assert_not_called()

    def test_failed_publish_entry_returns_500_with_the_saved_order_id(self):
        self.handler.events.put_events.side_effect = None
        self.handler.events.put_events.return_value = {
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "boom"}],
        }
        status, body = self.invoke(order())
        self.assertEqual(status, 500)
        self.assertIn("orderId", body)
        self.assertIn("event publish failed", body["message"])

    def test_publish_exception_returns_500_with_the_saved_order_id(self):
        self.handler.events.put_events.side_effect = BotoCoreError("network down")
        status, body = self.invoke(order())
        self.assertEqual(status, 500)
        self.assertIn("orderId", body)


if __name__ == "__main__":
    unittest.main()
