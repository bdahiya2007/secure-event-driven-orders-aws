import hashlib
import importlib.util
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vendor_awscrt.py"
spec = importlib.util.spec_from_file_location("vendor_awscrt", SCRIPT)
vendor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vendor)


def make_wheel(path, extra=None):
    members = {
        "awscrt/__init__.py": b"# awscrt",
        "_awscrt.abi3.so": b"\x7fELF",  # the real wheel keeps the compiled extension at its root
        f"awscrt-{vendor.VERSION}.dist-info/METADATA": b"Name: awscrt",
        "unrelated/readme.txt": b"not needed",
    }
    members.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.handler = self.root / "handler.py"
        self.handler.write_text("def lambda_handler(event, context):\n    return {}\n")
        self.out = self.root / ".build" / "package"
        (self.root / ".build" / "cache").mkdir(parents=True)

    def with_wheel(self, extra=None, sha_override=None):
        wheel = self.root / ".build" / "cache" / vendor.WHEEL_NAME
        sha = make_wheel(wheel, extra)
        return mock.patch.object(vendor, "WHEEL_SHA256", sha_override or sha)

    def test_the_package_holds_the_handler_and_awscrt_only(self):
        with self.with_wheel():
            vendor.build(str(self.handler), str(self.out))
        names = sorted(str(p.relative_to(self.out)) for p in self.out.rglob("*") if p.is_file())
        self.assertEqual(
            names,
            ["_awscrt.abi3.so", "awscrt-%s.dist-info/METADATA" % vendor.VERSION, "awscrt/__init__.py", "handler.py"],
        )

    def test_a_wheel_that_does_not_match_the_pin_is_refused_and_nothing_is_unpacked(self):
        with self.with_wheel(sha_override="0" * 64), mock.patch.object(vendor.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                vendor.build(str(self.handler), str(self.out))
        self.assertFalse(self.out.exists())

    def test_a_download_with_the_wrong_hash_is_rejected_and_not_cached(self):
        (self.root / ".build" / "cache" / vendor.WHEEL_NAME).unlink(missing_ok=True)

        class Response:
            def __init__(self):
                self.data = b"tampered"

            def read(self, n=-1):
                chunk, self.data = self.data, b""
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(vendor.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaises(vendor.VendorError):
                vendor.fetch_wheel(str(self.root / ".build" / "cache"))
        self.assertEqual(list((self.root / ".build" / "cache").iterdir()), [])

    def test_a_path_that_escapes_the_package_is_rejected(self):
        with self.with_wheel(extra={"awscrt/../../evil.py": b"x"}):
            with self.assertRaises(vendor.VendorError):
                vendor.build(str(self.handler), str(self.out))
        self.assertFalse((self.root / "evil.py").exists())

    def test_building_twice_gives_identical_files_and_timestamps(self):
        with self.with_wheel():
            vendor.build(str(self.handler), str(self.out))
            first = {str(p.relative_to(self.out)): (p.read_bytes(), int(p.stat().st_mtime)) for p in self.out.rglob("*") if p.is_file()}
            vendor.build(str(self.handler), str(self.out))
            second = {str(p.relative_to(self.out)): (p.read_bytes(), int(p.stat().st_mtime)) for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(first, second)
        self.assertEqual({stamp for _, stamp in first.values()}, {1577836800})

    def test_a_rebuild_removes_files_left_from_an_earlier_build(self):
        with self.with_wheel():
            vendor.build(str(self.handler), str(self.out))
            (self.out / "stale.py").write_text("old")
            vendor.build(str(self.handler), str(self.out))
        self.assertFalse((self.out / "stale.py").exists())

    def test_the_pin_is_a_stable_abi_wheel_for_the_lambda_architecture(self):
        self.assertIn("abi3", vendor.WHEEL_NAME)
        self.assertIn("x86_64", vendor.WHEEL_NAME)
        self.assertEqual(len(vendor.WHEEL_SHA256), 64)
        self.assertTrue(vendor.WHEEL_URL.startswith("https://files.pythonhosted.org/"))
        self.assertTrue(vendor.WHEEL_URL.endswith(vendor.WHEEL_NAME))


if __name__ == "__main__":
    unittest.main()
