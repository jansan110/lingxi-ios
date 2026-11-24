"""JavaScriptCore access through the app's own C ABI.

Why not ``import JavaScriptCore``: that name belongs to PyObjC, which does not
ship for iOS. The JSC framework is an Objective-C API, so it has to be owned by
the Swift side, which is the only layer with a live ObjC runtime here.

The bridge is therefore a handful of ``@_cdecl`` functions exported from
``JSCHost.swift``. Swift keeps one ``JSContext`` per handle, evaluates scripts
with ``JSContext.evaluateScript``, and calls back into Python through a C
function pointer for the synchronous HTTP hook the wheel runtime needs
(``globalThis.__py_http_json``).

Memory contract: every ``char*`` Swift returns is ``strdup``ed and must be freed
by the caller. The helpers below do that, so no Python object ever holds a
dangling C string.
"""

from __future__ import annotations

import ctypes
import threading

__all__ = ["JSCError", "Context", "available"]


class JSCError(Exception):
    """Raised when JavaScriptCore reports an exception or the bridge is absent."""


_lib = ctypes.CDLL(None)  # the app executable exports the functions
_symbols_ready = False
_symbol_error = ""


def _bind() -> None:
    global _symbols_ready, _symbol_error
    if _symbols_ready or _symbol_error:
        return
    try:
        _lib.lingxi_jsc_create.restype = ctypes.c_void_p
        _lib.lingxi_jsc_create.argtypes = []
        _lib.lingxi_jsc_destroy.restype = None
        _lib.lingxi_jsc_destroy.argtypes = [ctypes.c_void_p]
        _lib.lingxi_jsc_eval.restype = ctypes.c_void_p
        _lib.lingxi_jsc_eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        _lib.lingxi_jsc_error.restype = ctypes.c_void_p
        _lib.lingxi_jsc_error.argtypes = [ctypes.c_void_p]
        _lib.lingxi_jsc_clear_error.restype = None
        _lib.lingxi_jsc_clear_error.argtypes = [ctypes.c_void_p]
        _lib.lingxi_jsc_set_callback.restype = ctypes.c_int
        _lib.lingxi_jsc_set_callback.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        _lib.lingxi_jsc_free.restype = None
        _lib.lingxi_jsc_free.argtypes = [ctypes.c_void_p]
    except AttributeError as error:
        _symbol_error = str(error)
        return
    _symbols_ready = True


def available() -> bool:
    _bind()
    return _symbols_ready


def _take_string(pointer) -> "str | None":
    """Copy a ``char*`` out of the JSC bridge and free the original."""
    if not pointer:
        return None
    try:
        return ctypes.cast(pointer, ctypes.c_char_p).value.decode("utf-8", "replace")
    finally:
        _lib.lingxi_jsc_free(pointer)


class Context:
    """One ``JSContext``, with the same surface the runtime wrapper expects."""

    #: C signature of the synchronous Python hook: (userdata, payload) -> char*
    CALLBACK = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)

    _keep_alive: dict = {}
    _lock = threading.Lock()

    def __init__(self):
        _bind()
        if not _symbols_ready:
            raise JSCError("JavaScriptCore bridge unavailable: " + _symbol_error)
        handle = _lib.lingxi_jsc_create()
        if not handle:
            raise JSCError("JavaScriptCore context creation failed")
        self._handle = handle
        self._callback_ref = None

    # -- evaluation -----------------------------------------------------
    def eval(self, source: str) -> str:
        """Evaluate ``source`` and return the result as text (``"null"`` if void)."""
        if self._handle is None:
            raise JSCError("JavaScriptCore context is closed")
        result = _lib.lingxi_jsc_eval(self._handle, source.encode("utf-8"))
        if result:
            text = _take_string(result)
            return text if text is not None else "null"
        message = _take_string(_lib.lingxi_jsc_error(self._handle))
        _lib.lingxi_jsc_clear_error(self._handle)
        raise JSCError(message or "JavaScriptCore evaluation failed")

    # -- synchronous host hook -----------------------------------------
    def set_http_hook(self, hook) -> None:
        """Install ``hook(payload: str) -> str`` as ``globalThis.__py_http_json``.

        The pointer is kept alive on the instance for as long as the context
        lives; dropping it while JavaScript can still call would be a crash.
        """
        if self._handle is None:
            raise JSCError("JavaScriptCore context is closed")

        def trampoline(_userdata, payload):
            try:
                incoming = payload.decode("utf-8", "replace") if payload else ""
                outgoing = hook(incoming)
                if not isinstance(outgoing, str):
                    outgoing = "" if outgoing is None else str(outgoing)
                return ctypes.cast(
                    ctypes.c_char_p(outgoing.encode("utf-8")), ctypes.c_void_p).value
            except Exception as error:  # noqa: BLE001 - must never unwind into JSC
                fallback = '{"error":"' + str(error).replace('"', "'") + '"}'
                return ctypes.cast(
                    ctypes.c_char_p(fallback.encode("utf-8")), ctypes.c_void_p).value

        reference = self.CALLBACK(trampoline)
        if _lib.lingxi_jsc_set_callback(self._handle, ctypes.cast(reference, ctypes.c_void_p)):
            raise JSCError("JavaScriptCore rejected the HTTP hook")
        self._callback_ref = reference

    # -- teardown -------------------------------------------------------
    def close(self) -> None:
        if self._handle is not None:
            _lib.lingxi_jsc_destroy(self._handle)
            self._handle = None
        self._callback_ref = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
        return False
