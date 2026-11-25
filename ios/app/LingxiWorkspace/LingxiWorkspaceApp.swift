import SwiftUI

/// The iOS counterpart of `PyApplication` + `MainActivity`: one scene, one
/// window, one embedded service.
///
/// Deployment target is iOS 16, so `onChange(of:)` uses the single-argument
/// form; the two-argument (`initial:`) variant is iOS 17 and up.
@main
struct LingxiWorkspaceApp: App {
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.light)
        }
        .onChange(of: scenePhase) { phase in
            // Android returns START_STICKY from the Service; on iOS the closest
            // behaviour is letting the background assertion carry the process.
            if phase == .background {
                NotificationCenter.default.post(name: .lingxiDidEnterBackground, object: nil)
            }
        }
    }
}

extension Notification.Name {
    static let lingxiDidEnterBackground = Notification.Name("cn.pppyl.lingxiworkspace.didEnterBackground")
}
