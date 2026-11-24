"""iOS entry point: start the same Flask service Android runs, on loopback.

Android's ``android_runtime.start`` is the template. The differences are only
the ones the platform forces:

* iOS has no Chaquopy, so the ``java`` shim is installed into ``sys.modules``
  and ``cryptography`` is redirected to ``iosbridge.crypto`` before any business
  module is imported.
* There is no foreground Service, so the app keeps the process alive with a
  background task assertion instead; the HTTP service itself is identical
  (waitress on 127.0.0.1 with an ephemeral port).
* The session cookie is handed to ``WKWebView`` by the Swift shell rather than
  set through Android's CookieManager.

``start`` returns the JSON string the Swift side already expects from
``android_runtime.start``: ``{"url": ..., "token": ...}``.
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import threading

_server = None
_app = None


def bootstrap(files_dir: str) -> None:
    """Install the platform shims before the business package is imported."""
    root = str(Path(__file__).resolve().parent)
    if root not in sys.path:
        sys.path.insert(0, root)

    os.environ["LINGXI_ANDROID"] = "1"  # reuse the Android code path verbatim
    os.environ["LINGXI_PLATFORM"] = "ios"
    os.environ["LINGXI_DATA_DIR"] = os.path.join(files_dir, "workspace")

    import cryptography_shim  # noqa: F401  (registers the module tree)
    import java  # noqa: F401  (registers the jclass surface)


def start(files_dir: str) -> str:
    global _server, _app
    bootstrap(files_dir)

    from app import app
    from flask import jsonify, request
    from waitress import create_server

    _app = app
    token = secrets.token_urlsafe(32)

    def require_native_session():
        if not hmac.compare_digest(request.cookies.get("lingxi_mobile", ""), token):
            return jsonify(error="请从本地工具台应用打开页面。"), 403

    app.before_request(require_native_session)
    app.extensions["recharge_app"].before_request(require_native_session)

    _server = create_server(app, host="127.0.0.1", port=0, threads=32, channel_timeout=240)
    port = _server.effective_port
    app.extensions["network_settings"].attach(port, [("127.0.0.1", port)], override="ios")
    threading.Thread(target=_server.run, daemon=True, name="ios-http").start()

    for key in ("session_maintenance", "lingxi_scheduler", "checkin_scheduler",
                "monthly_coupon_scheduler", "star_day_scheduler",
                "net_age_surprise_scheduler", "platform_manager", "update_checker"):
        app.extensions[key].start()
    app.extensions["recharge_app"].extensions["scheduler"].start()

    return json.dumps({"url": f"http://127.0.0.1:{port}/", "token": token})


def stop() -> None:
    global _app
    if _app is not None:
        _app.extensions["session_maintenance"].stop()
        for key in ("lingxi_scheduler", "checkin_scheduler", "monthly_coupon_scheduler",
                    "star_day_scheduler", "net_age_surprise_scheduler",
                    "platform_manager", "update_checker"):
            _app.extensions[key].shutdown()
        _app.extensions["recharge_app"].extensions["scheduler"].stop()
        _app = None
    if _server is not None:
        _server.close()
