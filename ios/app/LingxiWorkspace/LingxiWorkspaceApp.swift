import SwiftUI

/// The iOS counterpart of `PyApplication` + `MainActivity`: one scene, one
/// window, one embedded service.
@main
struct LingxiWorkspaceApp: App {
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.light)
        }
        .onChange(of: scenePhase) { _, phase in
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
