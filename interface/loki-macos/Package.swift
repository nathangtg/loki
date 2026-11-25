// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LokiOrb",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "LokiOrb", targets: ["LokiOrb"])
    ],
    // No external dependencies of any kind.
    targets: [
        // Pure, Foundation-only logic shared by the app and the checks.
        .target(
            name: "LokiOrbCore",
            path: "Sources/LokiOrbCore"
        ),
        .executableTarget(
            name: "LokiOrb",
            dependencies: ["LokiOrbCore"],
            path: "Sources/LokiOrb"
        ),
        // Plain executable, run with `swift run LokiOrbChecks`. No test framework.
        .executableTarget(
            name: "LokiOrbChecks",
            dependencies: ["LokiOrbCore"],
            path: "Sources/LokiOrbChecks"
        ),
    ]
)
