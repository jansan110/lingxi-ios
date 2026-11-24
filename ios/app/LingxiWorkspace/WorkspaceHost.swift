import Foundation
import SwiftUI
import WebKit

/// Owns the Python service lifecycle and the WKWebView session cookie.
///
/// This is the iOS counterpart of `WorkspaceService` plus the `awaitReady`
/// runnable in Android's `MainActivity`: start CPython on a background thread,
/// call `ios_runtime.start(files_dir)`, keep the returned token, and install it
/// as the `lingxi_mobile` cookie before the page loads.
@MainActor
final class WorkspaceHost: ObservableObject {
    @Published private(set) var isReady = false
    @Published private(set) var statusText = "正在启动手机本机服务…"
    @Published private(set) var failure: String?
    @Published var showBackgroundInfo = false

    private var webView: WKWebView?
    private var serviceURL: URL?
    private var sessionToken = ""
    private var started = false
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid

    private var dataDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let directory = base.appendingPathComponent("workspace", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    init() {
        start()
    }

    func attach(_ web: WKWebView) {
        webView = web
        if isReady, let url = serviceURL {
            load(url)
        }
    }

    func start() {
        guard !started else { return }
        started = true
        failure = nil
        statusText = "正在启动手机本机服务…"
        beginBackgroundAssertion()

        let directory = dataDirectory.path
        DispatchQueue.global(qos: .userInitiated).async {
            let result = PythonBridge.startService(filesDirectory: directory)
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                switch result {
                case .success(let payload):
                    guard let url = URL(string: payload.url) else {
                        self.markFailed("本机服务返回了无效地址。")
                        return
                    }
                    self.sessionToken = payload.token
                    self.serviceURL = url
                    self.applyCookie { [weak self] in
                        guard let self, let web = self.webView else { return }
                        self.load(url, in: web)
                    }
                case .failure(let message):
                    self.markFailed(message)
                }
            }
        }
    }

    /// Writes `lingxi_mobile=<token>` into the shared cookie store, matching
    /// Android's `CookieManager.setCookie(url, "lingxi_mobile=…; Path=/; HttpOnly; SameSite=Strict")`.
    func applyCookie(completion: @escaping () -> Void) {
        guard let url = serviceURL, !sessionToken.isEmpty else {
            completion()
            return
        }
        let properties: [HTTPCookiePropertyKey: Any] = [
            .name: "lingxi_mobile",
            .value: sessionToken,
            .domain: url.host ?? "127.0.0.1",
            .path: "/",
            .secure: false,
            .discard: false,
        ]
        guard let cookie = HTTPCookie(properties: properties) else {
            completion()
            return
        }
        WKWebsiteDataStore.default().httpCookieStore.setCookie(cookie) {
            DispatchQueue.main.async(execute: completion)
        }
    }

    private func load(_ url: URL, in web: WKWebView? = nil) {
        guard let target = web ?? webView else { return }
        target.load(URLRequest(url: url))
    }

    func markLoaded() {
        isReady = true
    }

    func markFailed(_ message: String) {
        failure = message
        statusText = message
    }

    func reload() {
        guard isReady, let url = serviceURL else {
            started = false
            start()
            return
        }
        applyCookie { [weak self] in
            guard let self else { return }
            self.webView?.load(URLRequest(url: url))
        }
    }

    func shutdown() {
        PythonBridge.stopService()
        endBackgroundAssertion()
        exit(0)
    }

    // MARK: - Background execution
    //
    // Android uses a foreground Service with a persistent notification. iOS has
    // no such thing for this app class, so the closest equivalent is a
    // long-running background task assertion: it keeps the process (and with it
    // the local HTTP server and the schedulers) alive while the app is
    // backgrounded, which is what the notification was there to signal.

    private func beginBackgroundAssertion() {
        guard backgroundTask == .invalid else { return }
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "LingxiWorkspace") { [weak self] in
            self?.endBackgroundAssertion()
        }
    }

    private func endBackgroundAssertion() {
        guard backgroundTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(backgroundTask)
        backgroundTask = .invalid
    }
}

/// Result shape returned by `ios_runtime.start`.
struct ServicePayload: Decodable {
    let url: String
    let token: String
}
