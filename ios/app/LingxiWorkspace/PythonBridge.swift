import Foundation
import PythonKit

/// Bridges Swift to the embedded CPython runtime.
///
/// Android uses Chaquopy (`Python.start(AndroidPlatform)`) and calls
/// `android_runtime.start(files_dir)`. iOS embeds CPython directly through the
/// `PythonKit`/`Python` module built by the `Python-Apple-support` package, and
/// calls the equivalent `ios_runtime.start(files_dir)`.
///
/// The Python standard library and site-packages are unpacked into the app
/// bundle at build time by `Scripts/build_python_framework.sh`; `PYTHONHOME` is
/// pointed at that directory once, before any import.
enum PythonBridge {
    struct Failure: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    private static let queue = DispatchQueue(label: "cn.pppyl.lingxiworkspace.python")
    private static var initialized = false

    /// Prepares the interpreter. Must run before `startService`.
    private static func initializeIfNeeded() throws {
        guard !initialized else { return }
        guard let resource = Bundle.main.resourceURL else {
            throw Failure(message: "应用资源目录缺失。")
        }
        let home = resource.appendingPathComponent("python", isDirectory: true)
        guard FileManager.default.fileExists(atPath: home.path) else {
            throw Failure(message: "内置 Python 运行库缺失，请重新安装应用。")
        }
        setenv("PYTHONHOME", home.path, 1)
        setenv("PYTHONPATH", home.appendingPathComponent("lib/python3.12").path, 1)
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1)
        setenv("PYTHONUTF8", "1", 1)
        setenv("LINGXI_BUNDLE", resource.path, 1)
        initialized = true
    }

    static func startService(filesDirectory: String) -> Result<ServicePayload, Failure> {
        do {
            try initializeIfNeeded()
            let payload = try queue.sync { () -> ServicePayload in
                let python = try Python.attemptImport("ios_runtime")
                let raw = python.start(filesDirectory: filesDirectory)
                guard let json = raw.description.data(using: .utf8) else {
                    throw Failure(message: "本机服务返回了无法解析的数据。")
                }
                return try JSONDecoder().decode(ServicePayload.self, from: json)
            }
            return .success(payload)
        } catch let failure as Failure {
            return .failure(failure)
        } catch {
            return .failure(Failure(message: "本机服务启动失败，请关闭后重试。"))
        }
    }

    static func stopService() {
        queue.sync {
            guard let python = try? Python.attemptImport("ios_runtime") else { return }
            _ = python.stop()
        }
    }
}
