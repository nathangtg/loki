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
    targets: [
        .executableTarget(
            name: "LokiOrb",
            path: "Sources/LokiOrb"
        )
    ]
)
