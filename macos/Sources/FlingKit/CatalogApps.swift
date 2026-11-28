// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: the Catalog's TV apps, as in desktop/fling_catalog.py. The signer pins were read
// from each project's current official APK (and, for F-Droid, from its signed index). A
// developer who changes keys makes the install stop with "signed by someone else" until
// the pin here is updated — the safe failure.
import Foundation

public struct CatalogApp: Sendable, Identifiable {
    public let key: String
    public let title: String
    public let pkg: String
    public let purpose: String            // English; the UI translates it
    public let license: String
    public let licenseURL: String
    public let homepage: String
    public let source: CatalogSource
    public let signers: [String]          // SHA-256 of the developer's signing certificate(s)
    public let tile: (letters: String, color: String)
    public var id: String { key }
    public var sourceURL: String { source.page }

    public init(key: String, title: String, pkg: String, purpose: String, license: String,
                licenseURL: String, homepage: String, source: CatalogSource, signers: [String],
                tile: (String, String)) {
        self.key = key; self.title = title; self.pkg = pkg; self.purpose = purpose
        self.license = license; self.licenseURL = licenseURL; self.homepage = homepage
        self.source = source; self.signers = signers; self.tile = (tile.0, tile.1)
    }
}

public enum Catalog {
    public static let abis = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]
    public static let defaultABIs = ["armeabi-v7a"]     // what is shown before the TV said what it runs

    public static let apps: [CatalogApp] = [
        CatalogApp(
            key: "vlc", title: "VLC", pkg: "org.videolan.vlc",
            purpose: "A player that opens almost any file or stream — for when Cast can't handle the format.",
            license: "GPL-2.0", licenseURL: "https://code.videolan.org/videolan/vlc-android/-/blob/master/COPYING",
            homepage: "https://www.videolan.org/vlc/download-android.html",
            source: MirrorbitsSource("VideoLAN", "https://get.videolan.org/vlc-android/last/",
                                     Dictionary(uniqueKeysWithValues: abis.map { ($0, "https://get.videolan.org/vlc-android/last/") }),
                                     #"VLC-Android-(?<v>\d+(?:\.\d+)+)-{abi}\.apk"#),
            signers: ["c8768d2cea0c4b622e419b4b4715981946821e4ebc035fb41776cad395a7f68e"], tile: ("VLC", "#f07c00")),
        CatalogApp(
            key: "kodi", title: "Kodi", pkg: "org.xbmc.kodi",
            purpose: "A media centre: films, series and music from your home network, with artwork and descriptions.",
            license: "GPL-2.0", licenseURL: "https://github.com/xbmc/xbmc/blob/master/LICENSE.md",
            homepage: "https://kodi.tv/download/android/",
            source: MirrorbitsSource("Kodi", "https://mirrors.kodi.tv/releases/android/", [
                "arm64-v8a": "https://mirrors.kodi.tv/releases/android/arm64-v8a/",
                "armeabi-v7a": "https://mirrors.kodi.tv/releases/android/arm/"],
                #"kodi-(?<v>\d+(?:\.\d+)+)-[A-Za-z]+-{abi}\.apk"#),
            signers: ["f517b44b5db5e62a6c1ec55ba47526db7de0d61f6ba26a7987520e293499b8d5"], tile: ("K", "#17a2d8")),
        CatalogApp(
            key: "jellyfin", title: "Jellyfin", pkg: "org.jellyfin.androidtv",
            purpose: "The client for your own Jellyfin media server: your film and series library on the TV.",
            license: "GPL-2.0", licenseURL: "https://github.com/jellyfin/jellyfin-androidtv/blob/master/LICENSE",
            homepage: "https://jellyfin.org/downloads/",
            source: GitHubSource("jellyfin/jellyfin-androidtv", ["": #"jellyfin-androidtv-v[\d.]+-release\.apk"#]),
            signers: ["d881796ed2a67ff6ef9f676828723c6b1fa18e09388962cba4abc4a594a69131"], tile: ("J", "#8e4fc4")),
        CatalogApp(
            key: "moonlight", title: "Moonlight", pkg: "com.limelight",
            purpose: "Your computer's games and desktop on the TV, via Sunshine or NVIDIA GameStream.",
            license: "GPL-3.0", licenseURL: "https://github.com/moonlight-stream/moonlight-android/blob/master/LICENSE.txt",
            homepage: "https://moonlight-stream.org/",
            source: GitHubSource("moonlight-stream/moonlight-android", ["": #"app-nonRoot-release\.apk"#]),
            signers: ["d6ce3a4df15060fe488fe52441a549dee4ba199b36e2cb1ed9085cca8bfa1aca"], tile: ("M", "#3f51b5")),
        CatalogApp(
            key: "localsend", title: "LocalSend", pkg: "org.localsend.localsend_app",
            purpose: "Sends files between the TV, phones and computers over Wi-Fi, no internet needed.",
            license: "Apache-2.0", licenseURL: "https://github.com/localsend/localsend/blob/main/LICENSE",
            homepage: "https://localsend.org/",
            source: GitHubSource("localsend/localsend", [
                "arm64-v8a": #"LocalSend-[\d.]+-android-arm64v8\.apk"#,
                "armeabi-v7a": #"LocalSend-[\d.]+-android-arm32v7\.apk"#,
                "x86_64": #"LocalSend-[\d.]+-android-x64\.apk"#]),
            signers: ["3220c353a73cfbd0c2f3052471c445324cf452bcba26de1c473a52fe5c44e1d6"], tile: ("LS", "#0f9d8a")),
        CatalogApp(
            key: "tailscale", title: "Tailscale", pkg: "com.tailscale.ipn",
            purpose: "Your own private network: reach the TV from your phone and computer even away from home. After installing, open it on the TV and sign in.",
            license: "BSD-3-Clause", licenseURL: "https://github.com/tailscale/tailscale-android/blob/main/LICENSE",
            homepage: "https://tailscale.com/download/android",
            source: TailscaleSource(),
            signers: ["5cdb295551bfe1a087fed6acda07141c6c929fa7c29bd273a7092813acc434bf"], tile: ("TS", "#242424")),
        CatalogApp(
            key: "files", title: "Material Files", pkg: "me.zhanghai.android.files",
            purpose: "A file manager: open what Fling copied to the TV's Download folder.",
            license: "GPL-3.0", licenseURL: "https://github.com/zhanghai/MaterialFiles/blob/master/LICENSE",
            homepage: "https://github.com/zhanghai/MaterialFiles",
            source: GitHubSource("zhanghai/MaterialFiles", ["": #"app-release-universal\.apk"#]),
            signers: ["873b9b60c77cf7f3cd5fae66d0fe112c4a86973e118ee8a29c346c4c673c97f0"], tile: ("MF", "#3c8d40")),
        CatalogApp(
            key: "fdroid", title: "F-Droid", pkg: "org.fdroid.fdroid",
            purpose: "A store of free apps: other open-source programs and their updates.",
            license: "GPL-3.0", licenseURL: "https://gitlab.com/fdroid/fdroidclient/-/blob/master/LICENSE",
            homepage: "https://f-droid.org/",
            source: FDroidSource("org.fdroid.fdroid"),
            signers: [FDroidIndex.repoCert], tile: ("F", "#1976d2")),
    ]

    public static func app(_ key: String) -> CatalogApp? { apps.first { $0.key == key } }
}
