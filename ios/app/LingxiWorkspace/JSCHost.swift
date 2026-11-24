import Foundation
import JavaScriptCore

/// JavaScriptCore host exported to Python through the C ABI.
///
/// Android embeds Javet/V8 inside its Java layer. iOS ships JavaScriptCore, but
/// it is an Objective-C framework and PyObjC does not exist on iOS -- so the
/// context has to live here, in the one layer that owns an ObjC runtime. Python
/// reaches it through the flat functions below (`iosbridge/jsc.py` binds them
/// with ctypes against the app executable).
///
/// Contract for every function below:
/// * returned `char*` is `strdup`ed and the caller frees it with
///   `lingxi_jsc_free`
/// * `NULL` from `lingxi_jsc_eval` means "an exception was thrown"; the message
///   is then taken with `lingxi_jsc_error`
@_cdecl("lingxi_jsc_create")
public func lingxi_jsc_create() -> UnsafeMutableRawPointer? {
    let host = JSCHost()
    return Unmanaged.passRetained(host).toOpaque()
}

@_cdecl("lingxi_jsc_destroy")
public func lingxi_jsc_destroy(_ handle: UnsafeMutableRawPointer?) {
    guard let handle else { return }
    Unmanaged<JSCHost>.fromOpaque(handle).release()
}

@_cdecl("lingxi_jsc_eval")
public func lingxi_jsc_eval(_ handle: UnsafeMutableRawPointer?, _ source: UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>? {
    guard let handle, let source else { return nil }
    let host = Unmanaged<JSCHost>.fromOpaque(handle).takeUnretainedValue()
    return host.evaluate(String(cString: source))
}

@_cdecl("lingxi_jsc_error")
public func lingxi_jsc_error(_ handle: UnsafeMutableRawPointer?) -> UnsafeMutablePointer<CChar>? {
    guard let handle else { return nil }
    let host = Unmanaged<JSCHost>.fromOpaque(handle).takeUnretainedValue()
    return host.takeError()
}

@_cdecl("lingxi_jsc_clear_error")
public func lingxi_jsc_clear_error(_ handle: UnsafeMutableRawPointer?) {
    guard let handle else { return }
    let host = Unmanaged<JSCHost>.fromOpaque(handle).takeUnretainedValue()
    host.clearError()
}

/// Installs `globalThis.__py_http_json`.
///
/// The C function pointer receives the opaque userdata (an unmanaged box that
/// retains the Swift closure) and the UTF-8 payload. It returns a `char*` the
/// JS bridge owns and frees, mirroring `JSValue`'s block lifetime.
@_cdecl("lingxi_jsc_set_callback")
public func lingxi_jsc_set_callback(_ handle: UnsafeMutableRawPointer?,
                                    _ callback: UnsafeMutableRawPointer?) -> Int32 {
    guard let handle, let callback else { return -1 }
    let host = Unmanaged<JSCHost>.fromOpaque(handle).takeUnretainedValue()
    typealias Hook = @convention(c) (UnsafeMutableRawPointer?, UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>?
    let function = unsafeBitCast(callback, to: Hook.self)
    host.installHTTPHook(function)
    return 0
}

@_cdecl("lingxi_jsc_free")
public func lingxi_jsc_free(_ pointer: UnsafeMutablePointer<CChar>?) {
    free(pointer)
}

// MARK: -

/// Owns one `JSContext` plus the pending exception text.
final class JSCHost {
    private let context: JSContext
    private var lastError: String?

    init() {
        context = JSContext()!
        context.exceptionHandler = { [weak self] _, exception in
            self?.lastError = exception?.toString() ?? "JavaScriptCore exception"
        }
    }

    func evaluate(_ source: String) -> UnsafeMutablePointer<CChar>? {
        lastError = nil
        guard let value = context.evaluateScript(source) else { return nil }
        if lastError != nil { return nil }
        if value.isUndefined { return strdup("null") }
        if value.isNull { return strdup("null") }
        return strdup(value.toString() ?? "null")
    }

    func takeError() -> UnsafeMutablePointer<CChar>? {
        defer { lastError = nil }
        return strdup(lastError ?? "JavaScriptCore evaluation failed")
    }

    func clearError() { lastError = nil }

    /// Wraps the Python function pointer as a JS function and installs it.
    func installHTTPHook(_ function: @escaping @convention(c) (UnsafeMutableRawPointer?, UnsafePointer<CChar>?) -> UnsafeMutablePointer<CChar>?) {
        let block: @convention(block) (String) -> String = { payload in
            guard let result = payload.withCString({ function(nil, $0) }) else { return "{}" }
            defer { free(result) }
            return String(cString: result)
        }
        let bridged = unsafeBitCast(block, to: AnyObject.self)
        let jsValue = JSValue(object: bridged, in: context)
        context.setObject(jsValue, forKeyedSubscript: "__py_http_json" as NSString)
    }
}
