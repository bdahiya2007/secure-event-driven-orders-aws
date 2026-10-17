"""Offline stand-ins for boto3 and botocore, so the Lambda handlers can be tested with only the
standard library: no AWS credentials, no network, nothing to install."""
import importlib.util
import logging
import os
import sys
import types
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
