// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// swift-testing (not XCTest): runs with Command Line Tools alone, which ship no XCTest.
import Foundation
import Testing
@testable import FlingKit

struct ToolLocatorTests {
    @Test func parseVersion() {
        #expect(ToolVersion.parse("Version 35.0.2-12147458") == [35, 0, 2, 12147458])
        #expect(ToolVersion.parse("v3.1") == [3, 1])
        #expect(ToolVersion.parse("1.2.3.4.5") == [1, 2, 3, 4])
        #expect(ToolVersion.parse(nil) == [])
        #expect(ToolVersion.parse("no digits") == [])
    }

    @Test func compareLikePythonTuples() {
        #expect(ToolVersion.less([1, 2], [1, 2, 0]))
        #expect(ToolVersion.less([1, 2, 9], [1, 3]))
        #expect(!ToolVersion.less([2, 0], [1, 9, 9]))
        #expect(ToolVersion.isNewer("3.2", than: "3.1.1"))
        #expect(!ToolVersion.isNewer("3.1", than: "3.1"))
        #expect(!ToolVersion.isNewer(nil, than: "3.1"))
        #expect(!ToolVersion.isNewer("3.1", than: nil))
    }

    @Test func extractVersionFromToolOutput() {
        let adb = ToolLocator.spec("adb")!, scrcpy = ToolLocator.spec("scrcpy")!, catt = ToolLocator.spec("catt")!
        let adbOut = "Android Debug Bridge version 1.0.41\nVersion 35.0.2-12147458\nInstalled as /opt/homebrew/bin/adb\n"
        #expect(ToolVersion.extract(adbOut, pattern: adb.versionPattern) == "35.0.2")
        #expect(ToolVersion.extract("scrcpy 3.1 <https://github.com/Genymobile/scrcpy>\n", pattern: scrcpy.versionPattern) == "3.1")
        #expect(ToolVersion.extract("catt v0.12.13\n", pattern: catt.versionPattern) == "0.12.13")
        #expect(ToolVersion.extract("garbage", pattern: adb.versionPattern) == nil)
    }

    @Test func minimumVersion() {
        let adb = ToolLocator.spec("adb")!, scrcpy = ToolLocator.spec("scrcpy")!
        #expect(!ToolLocator.compatible("29.0.6", spec: adb))
        #expect(ToolLocator.compatible("30.0.0", spec: adb))
        #expect(!ToolLocator.compatible("2.3.1", spec: scrcpy))
        #expect(ToolLocator.compatible("2.4", spec: scrcpy))
        #expect(ToolLocator.compatible(nil, spec: scrcpy))
    }

    @Test func sortPutsCompatibleNewestFirst() {
        let copies = [ToolCopy(path: "/a", source: "path", version: "2.3", compatible: false),
                      ToolCopy(path: "/b", source: "brew", version: "2.7", compatible: true),
                      ToolCopy(path: "/c", source: "sdk", version: "3.1", compatible: true)]
        #expect(ToolLocator.sorted(copies).map(\.path) == ["/c", "/b", "/a"])
    }

    @Test func commands() {
        let adb = ToolLocator.spec("adb")!, scrcpy = ToolLocator.spec("scrcpy")!, catt = ToolLocator.spec("catt")!
        let brew = "/opt/homebrew/bin/brew"
        #expect(ToolLocator.command(for: adb, have: nil, brew: brew, uv: nil)
                == ToolCommand(executable: brew, arguments: ["install", "--cask", "android-platform-tools"]))
        let brewCopy = ToolCopy(path: "/opt/homebrew/bin/scrcpy", source: "brew", version: "2.0", compatible: false)
        #expect(ToolLocator.command(for: scrcpy, have: brewCopy, brew: brew, uv: nil)?.arguments == ["upgrade", "scrcpy"])
        // not Homebrew's copy, or no Homebrew: plain instructions instead
        let sdkCopy = ToolCopy(path: "/x/platform-tools/adb", source: "sdk", version: "29", compatible: false)
        #expect(ToolLocator.command(for: adb, have: sdkCopy, brew: brew, uv: nil) == nil)
        #expect(ToolLocator.command(for: scrcpy, have: nil, brew: nil, uv: nil) == nil)
        // catt goes through uv
        #expect(ToolLocator.command(for: catt, have: nil, brew: nil, uv: "/u/uv")?.arguments == ["tool", "install", "catt"])
        let uvCopy = ToolCopy(path: "/h/.local/bin/catt", source: "uv", version: "0.12", compatible: true)
        #expect(ToolLocator.command(for: catt, have: uvCopy, brew: brew, uv: "/u/uv")?.arguments == ["tool", "upgrade", "catt"])
        let boot = ToolLocator.command(for: catt, have: nil, brew: nil, uv: nil)
        #expect(boot?.executable == "/bin/sh")
        #expect(boot?.arguments.last?.contains("https://astral.sh/uv/install.sh") == true)
        #expect(ToolLocator.command(for: catt, have: nil, brew: brew, uv: nil)?.arguments.last?.contains("install uv") == true)
    }

    @Test func platformToolsVersionFromGoogleRepository() {
        let xml = """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <sdk:sdk-repository xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/03">
          <channel id="channel-0">stable</channel><channel id="channel-2">dev</channel>
          <remotePackage path="platform-tools">
            <revision><major>36</major><minor>0</minor><micro>0</micro></revision>
            <channelRef ref="channel-2"/>
          </remotePackage>
          <remotePackage path="platform-tools">
            <revision><major>35</major><minor>0</minor><micro>2</micro></revision>
            <channelRef ref="channel-0"/>
          </remotePackage>
          <remotePackage path="platform-tools">
            <revision><major>34</major><minor>0</minor><micro>5</micro></revision>
            <channelRef ref="channel-0"/>
          </remotePackage>
          <remotePackage path="build-tools;99.0.0">
            <revision><major>99</major></revision><channelRef ref="channel-0"/>
          </remotePackage>
        </sdk:sdk-repository>
        """
        #expect(ToolLocator.parsePlatformToolsVersion(Data(xml.utf8)) == "35.0.2")
        #expect(ToolLocator.parsePlatformToolsVersion(Data("<x/>".utf8)) == nil)
    }

    @Test func shortVersions() {
        #expect(shortVersion("1.104.7-tfb3b9b296-g226f029a3") == "1.104.7")
        #expect(shortVersion("beta") == "beta")
    }
}
