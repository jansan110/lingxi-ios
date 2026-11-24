"""The iOS platform layer must satisfy the same contract as the Android Java classes.

Android's five Java classes are mirrored in ``iosbridge``. These tests assert the
signatures and behaviours ``tests/test_android_transport.py`` relies on, so the
business layer cannot tell the two platforms apart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "LingxiWorkspace"
sys.path.insert(0, str(APP))

# Install the platform shims exactly as the app does at launch. Without this the
# business layer falls back to its desktop branches (curl_cffi, py_mini_racer,
# OpenCV), which is the wrong code path for this build.
import tempfile as _tempfile  # noqa: E402

_DATA = _tempfile.mkdtemp(prefix="ios-contract-")
os.environ.setdefault("LINGXI_DATA_DIR", _DATA)
import ios_runtime  # noqa: E402

ios_runtime.bootstrap(_DATA)


class NativeHttpContract(unittest.TestCase):
    def setUp(self):
        from iosbridge.bridge import NativeHttp
        self.NativeHttp = NativeHttp

    def test_result_exposes_the_java_method_names(self):
        from iosbridge.bridge import NativeHttpResult
        result = NativeHttpResult(200, [["Content-Type", "text/plain"]], b"hello")
        self.assertEqual(result.status(), 200)
        self.assertEqual(json.loads(result.headers()), [["Content-Type", "text/plain"]])
        self.assertEqual(result.read(), b"hello")
        self.assertIsNone(result.read())
        result.close()

    def test_result_streams_in_8k_blocks_and_ends_with_none(self):
        from iosbridge.bridge import NativeHttpResult
        payload = bytes(range(256)) * 100  # 25600 bytes
        result = NativeHttpResult(200, [], payload)
        blocks = []
        while True:
            block = result.read()
            if block is None:
                break
            blocks.append(block)
        self.assertEqual(b"".join(blocks), payload)
        self.assertTrue(all(len(block) <= 8192 for block in blocks))
        self.assertGreater(len(blocks), 1)

    def test_request_forwards_method_url_headers_and_body(self):
        captured = {}

        class FakeResponse:
            status_code = 201
            headers = {"X-Test": "1"}
            content = b'{"ok":true}'

            def __init__(self):
                self.headers = types.SimpleNamespace(multi_items=lambda: [("X-Test", "1")])

        class FakeClient:
            def build_request(self, method, url, headers=None, content=None, timeout=None):
                captured.update(method=method, url=url, headers=headers, content=content)
                return types.SimpleNamespace(extensions={})

            def send(self, request, stream=False, follow_redirects=False):
                return FakeResponse()

        self.NativeHttp._session = FakeClient()
        try:
            result = self.NativeHttp.request(
                "POST", "https://example.test/x", json.dumps([["a", "b"]]), b"body", True, 10, 30)
        finally:
            self.NativeHttp._session = None

        self.assertEqual(result.status(), 201)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://example.test/x")
        self.assertEqual(captured["headers"], [("a", "b")])
        self.assertEqual(captured["content"], b"body")
        self.assertEqual(result.read(), b'{"ok":true}')
        self.assertIsNone(result.read())

    def test_get_and_head_send_no_body(self):
        captured = {}

        class FakeClient:
            def build_request(self, method, url, headers=None, content=None, timeout=None):
                captured["content"] = content
                return types.SimpleNamespace(extensions={})

            def send(self, request, stream=False, follow_redirects=False):
                response = types.SimpleNamespace(
                    status_code=204, content=b"",
                    headers=types.SimpleNamespace(multi_items=lambda: []),
                )
                return response

        self.NativeHttp._session = FakeClient()
        try:
            self.NativeHttp.request("GET", "https://example.test/", "[]", b"ignored", False, 5, 5)
        finally:
            self.NativeHttp._session = None
        self.assertIsNone(captured["content"])

    def test_transport_errors_surface_as_java_ioexception(self):
        from iosbridge.bridge import JavaIOException

        class FakeClient:
            def build_request(self, *args, **kwargs):
                return types.SimpleNamespace(extensions={})

            def send(self, *args, **kwargs):
                import httpx

                raise httpx.ConnectError("boom")

        self.NativeHttp._session = FakeClient()
        try:
            with self.assertRaises(JavaIOException):
                self.NativeHttp.request("GET", "https://example.test/", "[]", b"", True, 5, 5)
        finally:
            self.NativeHttp._session = None


class CredentialVaultContract(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.directory = tempfile.mkdtemp(prefix="vault-")
        os.environ["LINGXI_DATA_DIR"] = self.directory
        # Force the file-backed fallback (no Keychain on the test host).
        os.environ.pop("LINGXI_KEYCHAIN_GET", None)
        os.environ.pop("LINGXI_KEYCHAIN_SET", None)
        import iosbridge.bridge as bridge

        bridge._KEYCHAIN_FALLBACK = None

    def test_round_trip_and_iv_prefix_format(self):
        from iosbridge.bridge import CredentialVault
        plain = b'{"cookie":"secret","phone":"13800000000"}'
        blob = CredentialVault.encrypt(plain)
        self.assertEqual(len(blob), 12 + len(plain) + 16)  # iv || ciphertext || tag
        self.assertEqual(CredentialVault.decrypt(blob), plain)

    def test_ciphertext_differs_per_call(self):
        from iosbridge.bridge import CredentialVault
        first = CredentialVault.encrypt(b"same")
        second = CredentialVault.encrypt(b"same")
        self.assertNotEqual(first[:12], second[:12])
        self.assertEqual(CredentialVault.decrypt(first), CredentialVault.decrypt(second))

    def test_key_is_not_stored_in_the_database_file(self):
        from iosbridge.bridge import CredentialVault
        CredentialVault.encrypt(b"x")
        key_file = Path(self.directory) / ".vault" / "vault.key"
        self.assertTrue(key_file.exists())
        self.assertEqual(len(key_file.read_bytes()), 32)
        self.assertEqual(key_file.stat().st_mode & 0o077, 0)


class SliderContract(unittest.TestCase):
    def test_android_slider_module_uses_the_opencv_shim(self):
        import base64
        import cv2
        import numpy as np

        rng = np.random.default_rng(11)
        ground = rng.integers(0, 256, (60, 200, 3), dtype=np.uint8)
        patch = ground[10:40, 77:107].copy()
        encode = lambda image: base64.b64encode(  # noqa: E731
            cv2.imencode(".png", image)[1]).decode()

        from activities.coc_slider import slide_position
        self.assertEqual(slide_position(encode(ground), encode(patch)), 77)

    def test_invalid_image_raises_the_business_error(self):
        from activities.coc_client import CocError
        from activities.coc_slider import slide_position
        with self.assertRaises(CocError) as caught:
            slide_position("invalid", "invalid")
        self.assertEqual(caught.exception.code, "SLIDER_RECOGNITION")


class JavaShimSurface(unittest.TestCase):
    def test_jclass_resolves_every_class_the_business_layer_asks_for(self):
        import java

        for name in ("cn.pppyl.lingxiworkspace.CredentialVault",
                     "cn.pppyl.lingxiworkspace.NativeHttp",
                     "cn.pppyl.lingxiworkspace.MemberBenefitsV8",
                     "java.io.IOException",
                     "org.opencv.android.OpenCVLoader",
                     "org.opencv.core.CvException"):
            with self.subTest(name=name):
                self.assertIsNotNone(java.jclass(name))

    def test_primitive_array_helpers_exist(self):
        import java
        self.assertEqual(java.jarray(java.jbyte)([1, 2, 255]), b"\x01\x02\xff")
        self.assertEqual(java.jbyte, int)

    def test_unknown_class_fails_loudly(self):
        import java
        with self.assertRaises(ImportError):
            java.jclass("com.example.NotImplemented")


if __name__ == "__main__":
    unittest.main(verbosity=2)
