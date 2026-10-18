#!/usr/bin/env python3
"""Assemble the CreateOrder package for a multi-region deployment: the handler plus a pinned, hash-checked awscrt.

Publishing through an EventBridge global endpoint needs SigV4A signing, which botocore only does with the awscrt
library. The Lambda runtime does not include it, so it is vendored into the function package.

This is used as a Terraform `external` data source: it reads {"handler": "<path>", "out": "<dir>"} as JSON on
stdin and prints {"dir": "<dir>"}. Only the Python standard library is used, so no pip is needed. The wheel is
downloaded once into a cache next to the output directory and its SHA-256 is checked against the pin below before
anything is unpacked. Run with --check to print the pin.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

# One pinned wheel: the stable-ABI build (works on Python 3.13 and later, including the 3.14 Lambda runtime) for
# x86_64 Linux. A release a few weeks old is pinned on purpose rather than the newest one.
VERSION = "0.37.0"
WHEEL_NAME = "awscrt-0.37.0-cp313-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/a1/14/636072e683138459d88dc02ef21e815f96f58a3658b05669752d9ac70f49/"
    + WHEEL_NAME
)
WHEEL_SHA256 = "d74761cfe977b39f2ae80810104b9f4ac15688f5438c322fb0f6124dc08b71ff"

KEEP_PREFIXES = ("awscrt/", f"awscrt-{VERSION}.dist-info/")
KEEP_FILES = ("_awscrt.abi3.so",)  # the compiled extension sits at the root of the wheel, not inside awscrt/
FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # so the package, and so its hash, is the same on every machine


class VendorError(Exception):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_wheel(cache_dir):
    """Return the path of the verified wheel, downloading it if the cache does not hold it."""
    os.makedirs(cache_dir, exist_ok=True)
    target = os.path.join(cache_dir, WHEEL_NAME)
    if os.path.exists(target) and _sha256(target) == WHEEL_SHA256:
        return target
    fd, partial = tempfile.mkstemp(dir=cache_dir, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(WHEEL_URL, timeout=60) as response:
            shutil.copyfileobj(response, out)
        if _sha256(partial) != WHEEL_SHA256:
            raise VendorError("The downloaded awscrt wheel does not match the pinned SHA-256; refusing to use it")
        os.replace(partial, target)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return target


def _safe_member(name):
    return not name.startswith("/") and ".." not in name.split("/")


def build(handler, out):
    """Write handler.py and the unpacked awscrt into `out` and return `out`."""
    wheel = fetch_wheel(os.path.join(os.path.dirname(os.path.abspath(out)), "cache"))
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copyfile(handler, os.path.join(out, "handler.py"))
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir() or not (info.filename.startswith(KEEP_PREFIXES) or info.filename in KEEP_FILES):
                continue
            if not _safe_member(info.filename):
                raise VendorError(f"Unsafe path in the wheel: {info.filename}")
            destination = os.path.join(out, info.filename)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with archive.open(info) as source, open(destination, "wb") as sink:
                shutil.copyfileobj(source, sink)
            os.chmod(destination, 0o755 if destination.endswith(".so") else 0o644)
    # Fixed timestamps make the zip reproducible, so Terraform sees no change between machines.
    stamp = (1577836800, 1577836800)
    for root, dirs, files in os.walk(out):
        for name in dirs + files:
            os.utime(os.path.join(root, name), stamp)
    os.utime(out, stamp)
    return out


def main(argv):
    if "--check" in argv:
        print(json.dumps({"version": VERSION, "wheel": WHEEL_NAME, "sha256": WHEEL_SHA256}))
        return 0
    query = json.load(sys.stdin)
    try:
        directory = build(os.path.abspath(query["handler"]), os.path.abspath(query["out"]))
    except (VendorError, OSError, KeyError) as error:
        print(f"vendor_awscrt: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"dir": directory}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
