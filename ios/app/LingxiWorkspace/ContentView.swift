import SwiftUI
import WebKit
import UIKit

/// The iOS counterpart of Android's `MainActivity`.
///
/// Android shows a `WebView` pointed at the loopback Flask service and injects
/// the `lingxi_mobile` session cookie through `CookieManager`. WKWebView has no
/// shared cookie store for a custom scheme, so the same cookie is written into
/// `WKWebsiteDataStore.default().httpCookieStore` before the first load and
/// re-applied on every navigation decision.
struct ContentView: View {
    @StateObject private var host = WorkspaceHost()

    var body: some View {
        NavigationStack {
            ZStack {
                Color(red: 0.957, green: 0.969, blue: 0.988).ignoresSafeArea()
                WorkspaceWebView(host: host)
                    .opacity(host.isReady ? 1 : 0)
                if !host.isReady {
                    LaunchStatusView(host: host)
                }
            }
            .navigationTitle("本地工具台")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("重新加载", systemImage: "arrow.clockwise") { host.reload() }
                        Button("后台运行说明", systemImage: "info.circle") { host.showBackgroundInfo = true }
                        Divider()
                        Button("停止并退出", systemImage: "power", role: .destructive) { host.shutdown() }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .accessibilityLabel("应用菜单")
                }
            }
            .alert("手机独立运行", isPresented: $host.showBackgroundInfo) {
                Button("打开设置") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
                Button("知道了", role: .cancel) {}
            } message: {
                Text("账号与任务全部在本机运行，关闭其他应用不影响。定时任务需要保持本应用在前台或后台存活；若系统回收、断网或低电量模式介入，任务可能推迟。启动后会补查当日签到。")
            }
        }
    }
}

/// Launch screen mirroring Android's status text while the Python service boots.
private struct LaunchStatusView: View {
    @ObservedObject var host: WorkspaceHost

    var body: some View {
        VStack(spacing: 16) {
            if let failure = host.failure {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 34))
                    .foregroundStyle(.orange)
                Text(failure)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.secondary)
                Button("重试") { host.start() }
                    .buttonStyle(.borderedProminent)
            } else {
                ProgressView()
                Text(host.statusText)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(28)
    }
}

/// WKWebView host that enforces the same navigation policy as Android.
private struct WorkspaceWebView: UIViewRepresentable {
    @ObservedObject var host: WorkspaceHost

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.websiteDataStore = .default()
        let web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = context.coordinator
        web.isOpaque = false
        web.backgroundColor = UIColor(red: 0.957, green: 0.969, blue: 0.988, alpha: 1)
        web.scrollView.contentInsetAdjustmentBehavior = .never
        host.attach(web)
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(host: host) }

    final class Coordinator: NSObject, WKNavigationDelegate {
        private let host: WorkspaceHost
        init(host: WorkspaceHost) { self.host = host }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            host.markLoaded()
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            host.markFailed("页面加载失败，请从菜单重新加载。")
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            host.markFailed("页面加载失败，请从菜单重新加载。")
        }

        /// Loopback stays inside the app; anything else opens in Safari, which is
        /// what Android does with `startActivity(ACTION_VIEW, uri)`.
        func webView(_ webView: WKWebView,
                     decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }
            if let hostName = url.host, hostName == "127.0.0.1" || hostName == "localhost" {
                host.applyCookie { decisionHandler(.allow) }
                return
            }
            if url.scheme == "http" || url.scheme == "https" {
                UIApplication.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }
    }
}
