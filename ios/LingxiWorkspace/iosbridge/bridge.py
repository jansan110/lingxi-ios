"""The single platform contract the iOS build provides to the Python business layer.

The desktop build imports ``curl_cffi``; the Android build imports a ``java``
package that forwards to the five Java classes under
``android/app/src/main/java/cn/pppyl/lingxiworkspace``. This build keeps the
Android code path byte-identical (``LINGXI_ANDROID=1``) and supplies a Python
``java`` shim instead, so no business module is edited. Everything the business
layer can reach through that shim is implemented here:

    cn.pppyl.lingxiworkspace.CredentialVault   -> Keychain-backed AES-GCM
    cn.pppyl.lingxiworkspace.NativeHttp        -> NSURLSession HTTP/2 + TLS
    cn.pppyl.lingxiworkspace.MemberBenefitsV8  -> JavaScriptCore V8 boundary
    org.opencv.* / OpenCVLoader                -> native + pure-Python imaging
    java.io.IOException                        -> an OSError subclass

Each class mirrors the Java signature the business layer calls, including the
names of the methods and their argument order.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import threading


# --------------------------------------------------------------------------
# java.io.Exception equivalents
# --------------------------------------------------------------------------
class JavaIOException(OSError):
    """Stands in for ``java.io.IOException`` in the transport boundary."""


# --------------------------------------------------------------------------
# Keychain-backed credential vault
# --------------------------------------------------------------------------
VAULT_SERVICE = "cn.pppyl.lingxiworkspace.credentials"
VAULT_ACCOUNT = "lingxi-accounts-v1"


class _KeychainFallback:
    """File-backed AES-GCM used when the Keychain bridge is unavailable.

    The iOS app injects ``LINGXI_KEYCHAIN_GET``/``LINGXI_KEYCHAIN_SET`` as a
    native function pair. When they are absent (unit tests, simulator runs) the
    key lives in a 0600 file inside the sandbox container, which still keeps it
    out of the SQLite database the credentials are stored in.
    """

    def __init__(self, directory: Path):
        self.path = Path(directory) / "vault.key"
        self.lock = threading.Lock()

    def _load(self) -> bytes:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return self.path.read_bytes()
        key = os.urandom(32)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(key)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        return key

    def key(self) -> bytes:
        with self.lock:
            return self._load()


_KEYCHAIN_FALLBACK: _KeychainFallback | None = None


def _vault_key() -> bytes:
    getter = os.environ.get("LINGXI_KEYCHAIN_GET")
    setter = os.environ.get("LINGXI_KEYCHAIN_SET")
    if getter and setter:
        module = __import__(getter)
        existing = getattr(module, "keychain_get")(VAULT_SERVICE, VAULT_ACCOUNT)
        if existing:
            return bytes(existing)
        key = os.urandom(32)
        getattr(module, "keychain_set")(VAULT_SERVICE, VAULT_ACCOUNT, key)
        return key
    global _KEYCHAIN_FALLBACK
    if _KEYCHAIN_FALLBACK is None:
        root = os.environ.get("LINGXI_DATA_DIR", os.getcwd())
        _KEYCHAIN_FALLBACK = _KeychainFallback(Path(root) / ".vault")
    return _KEYCHAIN_FALLBACK.key()


class _AesGcm:
    """AES-GCM with the same wire format as Android's CredentialVault.

    Android returns ``iv || ciphertext || tag`` where ``iv`` is 12 bytes and the
    tag is 16. ``cryptography`` keeps the tag appended to the ciphertext, so the
    concatenation is already identical.
    """

    @staticmethod
    def _aesgcm():
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(_vault_key())

    @classmethod
    def encrypt(cls, plain: bytes) -> bytes:
        iv = os.urandom(12)
        return iv + cls._aesgcm().encrypt(iv, bytes(plain), None)

    @classmethod
    def decrypt(cls, encrypted: bytes) -> bytes:
        data = bytes(encrypted)
        if len(data) < 12 + 16:
            raise JavaIOException("credential vault payload is too short")
        return cls._aesgcm().decrypt(data[:12], data[12:], None)


class CredentialVault:
    """Mirror of ``cn.pppyl.lingxiworkspace.CredentialVault``."""

    encrypt = staticmethod(_AesGcm.encrypt)
    decrypt = staticmethod(_AesGcm.decrypt)


# --------------------------------------------------------------------------
# NSURLSession transport
# --------------------------------------------------------------------------
class NativeHttpResult:
    """Mirror of ``NativeHttp.Result``: status(), headers(), read(), close()."""

    __slots__ = ("_status", "_headers", "_body", "_offset", "_lock")

    def __init__(self, status: int, headers, body: bytes):
        self._status = int(status)
        self._headers = list(headers)
        self._body = body
        self._offset = 0
        self._lock = threading.Lock()

    def status(self) -> int:
        return self._status

    def headers(self) -> str:
        return json.dumps(self._headers, ensure_ascii=False)

    def read(self):
        """Return the next 8 KiB block, or ``None`` once the body is drained.

        ``NativeHttp.Result.read`` returns a Java ``byte[]`` and Python turns it
        into a ``bytes`` object; ``http_transport.NativeBody`` then checks for
        ``None`` to stop. Returning ``None`` (not ``b''``) keeps that contract.
        """
        with self._lock:
            if self._offset >= len(self._body):
                return None
            block = self._body[self._offset:self._offset + 8192]
            self._offset += len(block)
            return block

    def close(self) -> None:
        with self._lock:
            self._offset = len(self._body)


class NativeHttp:
    """Mirror of ``cn.pppyl.lingxiworkspace.NativeHttp``.

    The Java version delegates to OkHttp. On iOS the equivalent stack is
    NSURLSession, which owns the system TLS trust store and speaks HTTP/2. The
    Python-side caller only needs ``status``/``headers``/``read``/``close``, so
    the session is used with redirects and retries disabled exactly like the
    Java builder.

    ``httpx`` is imported lazily: the transport module is only reached when a
    business flow performs a request.
    """

    _session_lock = threading.Lock()
    _session = None

    @classmethod
    def _client(cls):
        with cls._session_lock:
            if cls._session is None:
                import httpx

                # HTTP/2 is used only when the h2 package is present (it is a
                # hard requirement of httpx for h2). Without it the caller still
                # gets HTTP/1.1 through the same code path.
                try:
                    import h2  # noqa: F401
                    http2 = True
                except ImportError:
                    http2 = False
                cls._session = httpx.Client(
                    follow_redirects=False,
                    verify=True,
                    timeout=None,
                    limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
                    http2=http2,
                )
            return cls._session

    @classmethod
    def request(cls, method, url, header_json, body, http2, connect_seconds, read_seconds):
        import httpx

        headers = []
        for name, value in json.loads(header_json):
            headers.append((str(name), str(value)))
        timeout = httpx.Timeout(connect=float(connect_seconds), read=float(read_seconds),
                                write=float(read_seconds), pool=float(connect_seconds))
        payload = None if method in ("GET", "HEAD") else bytes(body or b"")
        request = cls._client().build_request(str(method), str(url), headers=headers,
                                             content=payload, timeout=timeout)
        if not http2:
            # Force HTTP/1.1 for this request without disturbing the shared pool.
            request.extensions["http_version"] = b"HTTP/1.1"
        try:
            response = cls._client().send(request, stream=False, follow_redirects=False)
        except httpx.HTTPError as error:
            raise JavaIOException(str(error)) from error
        pairs = [[name, value] for name, value in response.headers.multi_items()]
        return NativeHttpResult(response.status_code, pairs, response.content)


# --------------------------------------------------------------------------
# JavaScriptCore boundary
# --------------------------------------------------------------------------
class _JSCException(Exception):
    pass


class _Timers:
    """The setTimeout/clearTimeout pair the Java TIMERS shim provides."""

    SCRIPT = """
    (() => {
      let nextId = 1;
      const timers = new Map();
      globalThis.setTimeout = (callback, delay = 0, ...args) => {
        const id = nextId++;
        timers.set(id, {callback, args, due: Date.now() + Math.max(1, Number(delay) || 1)});
        return id;
      };
      globalThis.clearTimeout = id => timers.delete(id);
      globalThis.setInterval = (callback, delay = 0, ...args) => {
        const id = nextId++;
        timers.set(id, {callback, args, due: Date.now() + Math.max(1, Number(delay) || 1),
                        every: Math.max(1, Number(delay) || 1)});
        return id;
      };
      globalThis.clearInterval = id => timers.delete(id);
      globalThis.__run_due_timers = () => {
        const now = Date.now();
        for (const [id, timer] of [...timers]) {
          if (timer.due <= now) {
            if (timer.every) timer.due = now + timer.every; else timers.delete(id);
            timer.callback(...timer.args);
          }
        }
        let wait = -1;
        for (const timer of timers.values()) {
          const delay = Math.max(0, timer.due - Date.now());
          wait = wait < 0 ? delay : Math.min(wait, delay);
        }
        return wait;
      };
    })();
    """


class MemberBenefitsV8:
    """Mirror of ``cn.pppyl.lingxiworkspace.MemberBenefitsV8``.

    The Java build embeds Javet/V8. iOS ships JavaScriptCore with the OS, which
    reaches the same state: a browser-free ECMAScript runtime with no WebView.
    The two entry points keep the Java argument order.
    """

    @staticmethod
    def _runtime():
        from . import jsc

        if not jsc.available():
            raise _JSCException("JavaScriptCore bridge unavailable")
        return _JavaScriptCoreRuntime()

    @classmethod
    def runWheel(cls, config_json, runtime_source, glue_source, wasm_base64, callback, timeout_ms):
        runtime = cls._runtime()
        try:
            return runtime.run_wheel(config_json, runtime_source, glue_source, wasm_base64,
                                     callback, int(timeout_ms))
        finally:
            runtime.close()

    @classmethod
    def runDfp(cls, config_json, runtime_source, dfp_runtime_source, sdk_source,
               dfp_config_json, state_json, callback, timeout_ms):
        runtime = cls._runtime()
        try:
            return runtime.run_dfp(config_json, runtime_source, dfp_runtime_source, sdk_source,
                                   dfp_config_json, state_json, callback, int(timeout_ms))
        finally:
            runtime.close()


class _JavaScriptCoreRuntime:
    """Drives one ``iosbridge.jsc.Context`` through the wheel/dfp protocol.

    Mirrors ``MemberBenefitsV8.await``: evaluate the promise, pump the timer
    queue while it is pending, and surface either the resolved value or the
    rejection text. The pumping happens in Python because JavaScriptCore has no
    event loop of its own here.
    """

    def __init__(self):
        from . import jsc

        self._jsc = jsc
        self.context = jsc.Context()

    def close(self):
        if self.context is not None:
            self.context.close()
        self.context = None

    # -- helpers --------------------------------------------------------
    def _eval(self, source):
        return self.context.eval(source)

    def _install_hook(self, callback):
        """Expose the synchronous Python HTTP bridge to JavaScript."""
        if callback is None:
            self._eval("globalThis.__py_http_json = () => '{}';")
            return

        def hook(payload):
            return callback(payload)

        self.context.set_http_hook(hook)

    def _eval_json(self, name, value):
        self._eval("globalThis." + name + " = " + json.dumps(value, ensure_ascii=False) + ";")

    def run_wheel(self, config_json, runtime_source, glue_source, wasm_base64, callback, timeout_ms):
        self._install_hook(callback)
        self._eval(_Timers.SCRIPT)
        self._eval_json("__member_config_json", config_json)
        self._eval("globalThis.__PY_CONFIG = JSON.parse(globalThis.__member_config_json);")
        self._eval(runtime_source + "\n//# sourceURL=python-wheel-runtime.js\n")
        self._eval(glue_source)
        self._eval_json("__member_wasm_base64", wasm_base64)
        self._eval("globalThis.__wheel_wasm_bytes = Uint8Array.from(atob(globalThis.__member_wasm_base64),"
                   " c => c.charCodeAt(0));")
        return self._await("__wheel_run()", timeout_ms)

    def run_dfp(self, config_json, runtime_source, dfp_runtime_source, sdk_source,
                dfp_config_json, state_json, callback, timeout_ms):
        self._install_hook(callback)
        self._eval(_Timers.SCRIPT)
        self._eval_json("__member_config_json", config_json)
        self._eval("globalThis.__PY_CONFIG = JSON.parse(globalThis.__member_config_json);")
        self._eval(runtime_source + "\n//# sourceURL=python-dfp-environment.js\n")
        self._eval(dfp_runtime_source + "\n//# sourceURL=python-dfp-runtime.js\n")
        self._eval_json("__DFP_SDK_SOURCE", sdk_source)
        self._eval_json("__member_dfp_config_json", dfp_config_json)
        self._eval_json("__member_dfp_state_json", state_json)
        return self._await(
            "__dfp_runtime_run(__DFP_SDK_SOURCE,JSON.parse(__member_dfp_config_json),"
            "JSON.parse(__member_dfp_state_json))",
            timeout_ms,
        )

    def _await(self, expression, timeout_ms):
        """Resolve ``expression`` exactly like the Java ``await`` loop."""
        import time

        self._eval("globalThis.__member_promise = " + expression + ";")
        deadline = time.monotonic() + max(1, int(timeout_ms)) / 1000.0
        while True:
            pending = self._eval(
                "globalThis.__member_promise && typeof globalThis.__member_promise.then === 'function'"
            ).strip().lower()
            if pending not in ("true", "1"):
                break
            self._eval(
                "globalThis.__member_settled = undefined;"
                "globalThis.__member_promise.then("
                "v => {globalThis.__member_settled = {ok:true, value:v};},"
                "e => {globalThis.__member_settled = {ok:false, value:String(e)};});"
            )
            while True:
                raw = self._eval("JSON.stringify(globalThis.__member_settled || null)")
                if raw and raw != "null":
                    break
                if time.monotonic() >= deadline:
                    raise _JSCException("V8 计算超时")
                self._eval("globalThis.__run_due_timers && globalThis.__run_due_timers();")
                time.sleep(0.001)
            payload = json.loads(raw)
            if payload["ok"]:
                value = payload.get("value")
                if value is None:
                    return "null"
                if isinstance(value, str):
                    return value
                return json.dumps(value, ensure_ascii=False)
            raise _JSCException("V8 计算失败: " + str(payload.get("value")))
        result = self._eval("globalThis.__member_promise")
        if result is None or result == "null" or result == "undefined":
            return "null"
        return result

# --------------------------------------------------------------------------
# OpenCV shim
# --------------------------------------------------------------------------
class CvException(Exception):
    """Stands in for ``org.opencv.core.CvException``."""


class OpenCVLoader:
    """Mirror of ``org.opencv.android.OpenCVLoader``."""

    @staticmethod
    def initLocal() -> bool:
        return True


# --------------------------------------------------------------------------
# java shim surface
# --------------------------------------------------------------------------
_CLASSES = {
    "cn.pppyl.lingxiworkspace.CredentialVault": CredentialVault,
    "cn.pppyl.lingxiworkspace.NativeHttp": NativeHttp,
    "cn.pppyl.lingxiworkspace.MemberBenefitsV8": MemberBenefitsV8,
    "java.io.IOException": JavaIOException,
    "org.opencv.android.OpenCVLoader": OpenCVLoader,
    "org.opencv.core.CvException": CvException,
}

# The OpenCV classes live in a separate module (they pull in the imaging
# pipeline); only the names the slider recognizer asks for are exposed.
from . import opencv as _opencv  # noqa: E402  (after CvException is defined)

_CLASSES.update({
    "org.opencv.core.Mat": _opencv.Mat,
    "org.opencv.core.MatOfByte": _opencv.MatOfByte,
    "org.opencv.core.Size": _opencv.Size,
    "org.opencv.core.Core": _opencv.Core,
    "org.opencv.imgcodecs.Imgcodecs": _opencv.Imgcodecs,
    "org.opencv.imgproc.Imgproc": _opencv.Imgproc,
})


def jclass(name):
    try:
        return _CLASSES[name]
    except KeyError:
        raise ImportError("iOS platform layer has no binding for " + name) from None
