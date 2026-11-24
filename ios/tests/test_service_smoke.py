"""End-to-end smoke test of the iOS runtime against the real Flask service.

Starts ``ios_runtime.start`` exactly the way the Swift shell does, then walks
every page and API route the Android build exposes. This is the check that the
iOS platform layer is a drop-in replacement rather than a partial one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "LingxiWorkspace"
sys.path.insert(0, str(APP))

# Every page the Android build serves, plus the read-only API endpoints that do
# not require a China Mobile account. Route names come from the app's own
# url_map; anything that needs an account id is exercised separately.
PAGES = [
    "/", "/accounts", "/lingxi", "/lingxi/tasks", "/checkin", "/checkin/wechat",
    "/monthly-coupon", "/coupons", "/star-day", "/daily-gold", "/seven-day",
    "/member-benefits", "/mobile-rights", "/bills", "/net-age", "/platforms",
    "/information", "/access",
]
APIS = [
    "/api/status", "/api/features", "/api/network/status", "/api/access/status",
    "/api/coupons/accounts", "/api/checkin/status", "/api/star-day/status",
    "/api/monthly-coupon/status", "/api/net-age/accounts",
    "/api/daily-gold/meta", "/api/seven-day/meta",
]


class ServiceSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path(tempfile.mkdtemp(prefix="lingxi-ios-"))
        cls.errors = []
        import ios_runtime

        cls.runtime = ios_runtime
        payload = json.loads(ios_runtime.start(str(cls.data)))
        cls.url, cls.token = payload["url"], payload["token"]

    @classmethod
    def tearDownClass(cls):
        cls.runtime.stop()
        shutil.rmtree(cls.data, ignore_errors=True)

    def _get(self, path):
        request = urllib.request.Request(self.url.rstrip("/") + path)
        request.add_header("Cookie", "lingxi_mobile=" + self.token)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_pages_render(self):
        for path in PAGES:
            with self.subTest(path=path):
                status, body = self._get(path)
                self.assertEqual(status, 200, path)
                self.assertGreater(len(body), 200, path)
                self.assertNotIn(b"Traceback (most recent call last)", body, path)

    def test_read_only_apis_answer_json(self):
        for path in APIS:
            with self.subTest(path=path):
                status, body = self._get(path)
                self.assertEqual(status, 200, path)
                try:
                    json.loads(body)
                except json.JSONDecodeError:
                    self.fail(f"{path} did not return JSON: {body[:200]!r}")

    def test_runtime_guard_rejects_requests_without_the_session_cookie(self):
        request = urllib.request.Request(self.url.rstrip("/") + "/api/status")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=15)
        self.assertEqual(caught.exception.code, 403)

    def test_static_assets_are_served(self):
        for path in ("/static/style.css", "/static/app.js", "/static/theme.css"):
            with self.subTest(path=path):
                status, body = self._get(path)
                self.assertEqual(status, 200, path)
                self.assertGreater(len(body), 50, path)

    def test_recharge_app_is_mounted(self):
        status, body = self._get("/recharge/api/health")
        self.assertEqual(status, 200)
        self.assertIn(b"ok", body.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
