// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import SwiftUI
import AppKit
import FlingKit

/// Port: the Samsung (Smart View) part of the panel, under the remote when a
/// Samsung TV is selected — what the TV's remote connection is doing, and
/// screen mirroring.
///
/// Mirroring: the Windows build uses Miracast, which macOS doesn't have. Samsung
/// TVs from 2018 on take AirPlay instead, and that is macOS's own Screen
/// Mirroring; there's no public API to start it, so Fling opens the Displays
/// settings (its "+" menu lists AirPlay TVs) and says where else to find it.
struct SamsungRows: View {
    @ObservedObject var state: AppState
    @State private var showHint = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let line = state.samsungStatus {
                HStack(spacing: 6) {
                    Image(systemName: state.tvPaired ? "dot.radiowaves.left.and.right" : "wifi.exclamationmark")
                        .font(.system(size: 10))
                    Text(tr(line)).lineLimit(2)
                }
                .font(.system(size: 10.5)).foregroundStyle(.secondary)
                .padding(.horizontal, 14).padding(.top, 2).padding(.bottom, 4)
            }
            MenuRow(title: "Mirror screen (AirPlay)…", icon: "rectangle.on.rectangle") {
                SamsungMirror.openScreenMirroring()
                showHint = true
            }
            if showHint {
                Text(tr(SamsungMirror.hint))
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14).padding(.bottom, 6)
            }
        }
        .onChange(of: state.selectedDevice) { _, _ in showHint = false }
    }
}

enum SamsungMirror {
    static let hint = "In Displays, press + and choose the TV under AirPlay, or use Control Center ▸ Screen Mirroring. Samsung TVs from 2018 on take AirPlay; Miracast isn't available on macOS."

    static func openScreenMirroring() {
        let urls = ["x-apple.systempreferences:com.apple.Displays-Settings.extension",
                    "x-apple.systempreferences:com.apple.preference.displays"]
        for raw in urls {
            if let url = URL(string: raw), NSWorkspace.shared.open(url) { return }
        }
    }
}
