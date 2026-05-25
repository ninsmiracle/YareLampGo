// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "LampgoAudioTap",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "LampgoAudioTap", targets: ["LampgoAudioTap"])
    ],
    targets: [
        .executableTarget(name: "LampgoAudioTap")
    ]
)
