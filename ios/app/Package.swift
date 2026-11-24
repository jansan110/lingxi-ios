// swift-tools-version:5.9
import PackageDescription

// The iOS shell reuses two mature open-source projects instead of vendoring a
// CPython build:
//
//   * beeware/Python-Apple-support ships a prebuilt, Xcode-compatible CPython
//     for iOS arm64 (the same job Chaquopy does for Android).
//   * pvieito/PythonKit is the Swift <-> Python bridge.
//
// `Scripts/build_python_framework.sh` downloads the Python-Apple-support release
// matching `pythonVersion` and unpacks it into `Resources/python`, which is what
// `PythonBridge.initializeIfNeeded` sets `PYTHONHOME` to.
let package = Package(
    name: "LingxiWorkspace",
    platforms: [.iOS(.v16)],
    products: [
        .executable(name: "LingxiWorkspace", targets: ["LingxiWorkspace"]),
    ],
    dependencies: [
        .package(url: "https://github.com/pvieito/PythonKit.git", from: "0.4.0"),
    ],
    targets: [
        .executableTarget(
            name: "LingxiWorkspace",
            dependencies: [
                .product(name: "PythonKit", package: "PythonKit"),
            ],
            path: "LingxiWorkspace",
            resources: [
                .copy("Resources/python"),
                .copy("Resources/business"),
            ]
        ),
    ]
)
