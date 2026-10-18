"""Offline stand-ins for boto3 and botocore, so the Lambda handlers can be tested with only the
standard library: no AWS credentials, no network, nothing to install."""
import importlib.util
import logging
import os
import re
import sys
import types
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


class ClientError(Exception):
    def __init__(self, error_response, operation_name):
        super().__init__(f"{operation_name}: {error_response}")
        self.response = error_response
        self.operation_name = operation_name


class BotoCoreError(Exception):
    pass


class TransactionCanceledException(Exception):
    def __init__(self, reasons=()):
        super().__init__("transaction cancelled")
        self.response = {"CancellationReasons": [{"Code": code} for code in reasons]}


class ConditionalCheckFailedException(Exception):
    pass


class TypeSerializer:
    def serialize(self, value):
        if isinstance(value, str):
            return {"S": value}
        if isinstance(value, bool):
            return {"BOOL": value}
        if isinstance(value, (int, float)):
            return {"N": str(value)}
        raise TypeError(f"unsupported type {type(value)}")


class FakeDynamoDB:
    """A small in-memory DynamoDB that evaluates the condition expressions the handlers use.

    It supports put_item, transact_write_items and query on tables whose key is orderId, or orderId plus
    itemId. Conditions understand attribute_not_exists, =, <>, <, >, AND, OR and parentheses, with #names and
    :values, so tests exercise the real semantics of the conditional writes (including races).
    """

    def __init__(self):
        self.tables = {}
        self.counts = Counter()
        self.exceptions = types.SimpleNamespace(
            TransactionCanceledException=TransactionCanceledException,
            ConditionalCheckFailedException=ConditionalCheckFailedException,
        )

    # --- helpers ---
    @staticmethod
    def _key(item):
        return (item["orderId"]["S"], item["itemId"]["S"]) if "itemId" in item else (item["orderId"]["S"],)

    @staticmethod
    def _py(typed):
        if typed is None:
            return None
        if "N" in typed:
            return float(typed["N"])
        return typed.get("S")

    def _holds(self, expression, item, names, values):
        names, values = names or {}, values or {}
        tokens = re.findall(r"attribute_not_exists\(\s*[#\w]+\s*\)|\(|\)|<>|<=|>=|=|<|>|[#:]?\w+", expression)
        position = 0

        def operand(token):
            if token.startswith(":"):
                return self._py(values[token])
            name = names.get(token, token)
            return self._py(item.get(name)) if item is not None else None

        def parse_or():
            nonlocal position
            result = parse_and()
            while position < len(tokens) and tokens[position] == "OR":
                position += 1
                right = parse_and()
                result = result or right
            return result

        def parse_and():
            nonlocal position
            result = parse_atom()
            while position < len(tokens) and tokens[position] == "AND":
                position += 1
                right = parse_atom()
                result = result and right
            return result

        def parse_atom():
            nonlocal position
            token = tokens[position]
            if token == "(":
                position += 1
                result = parse_or()
                assert tokens[position] == ")"
                position += 1
                return result
            if token.startswith("attribute_not_exists"):
                position += 1
                name = re.search(r"\(\s*([#\w]+)\s*\)", token).group(1)
                return item is None or names.get(name, name) not in item
            left, op, right = tokens[position], tokens[position + 1], tokens[position + 2]
            position += 3
            a, b = operand(left), operand(right)
            if a is None or b is None:
                return False
            return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]

        result = parse_or()
        assert position == len(tokens), f"unparsed condition: {expression}"
        return result

    # --- the API the handlers use ---
    def put_item(self, TableName, Item, ConditionExpression=None, ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        self.counts[("put_item", TableName)] += 1
        table = self.tables.setdefault(TableName, {})
        existing = table.get(self._key(Item))
        if ConditionExpression and not self._holds(ConditionExpression, existing, ExpressionAttributeNames, ExpressionAttributeValues):
            raise ConditionalCheckFailedException()
        table[self._key(Item)] = Item
        return {}

    def transact_write_items(self, TransactItems):
        self.counts[("transact_write_items", None)] += 1
        reasons, failed = [], False
        for entry in TransactItems:
            put = entry["Put"]
            existing = self.tables.get(put["TableName"], {}).get(self._key(put["Item"]))
            ok = self._holds(put.get("ConditionExpression", "attribute_not_exists(orderId)"), existing,
                             put.get("ExpressionAttributeNames"), put.get("ExpressionAttributeValues"))
            reasons.append("None" if ok else "ConditionalCheckFailed")
            failed = failed or not ok
        if failed:
            raise TransactionCanceledException(reasons)
        for entry in TransactItems:
            put = entry["Put"]
            self.tables.setdefault(put["TableName"], {})[self._key(put["Item"])] = put["Item"]
        return {}

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues, **_ignored):
        self.counts[("query", TableName)] += 1
        order_id = ExpressionAttributeValues[":o"]["S"]
        items = [item for key, item in self.tables.get(TableName, {}).items() if key[0] == order_id]
        return {"Items": items}


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module


def _install_stubs():
    """Replace boto3 and botocore in sys.modules. Each boto3.client() call returns a fresh mock."""
    clients = {}

    def client(service, **_kwargs):
        mocked = mock.MagicMock(name=f"{service}-client")
        if service == "dynamodb":
            mocked.exceptions.TransactionCanceledException = TransactionCanceledException
            mocked.exceptions.ConditionalCheckFailedException = ConditionalCheckFailedException
        clients[service] = mocked
        return mocked

    sys.modules["boto3"] = _module("boto3", client=client)
    sys.modules["boto3.dynamodb"] = _module("boto3.dynamodb")
    sys.modules["boto3.dynamodb.types"] = _module("boto3.dynamodb.types", TypeSerializer=TypeSerializer)
    sys.modules["botocore"] = _module("botocore")
    sys.modules["botocore.config"] = _module("botocore.config", Config=lambda **kwargs: kwargs)
    sys.modules["botocore.exceptions"] = _module(
        "botocore.exceptions", ClientError=ClientError, BotoCoreError=BotoCoreError
    )
    return clients


def load_handler(relative_path, module_name, env):
    """Import a fresh copy of a handler with stubbed AWS libraries and the given environment."""
    logging.disable(logging.CRITICAL)  # the handlers log expected errors; keep test output readable
    _install_stubs()
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module
