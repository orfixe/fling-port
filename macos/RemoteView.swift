import SwiftUI
import Combine
import FlingKit

/// The TV section of the panel: power, app launcher, d-pad, volume keys, and
/// typing, all over the Android TV Remote session.
///
/// Proposed change vs main: the always-visible “Type on TV” field is replaced
/// by a keyboard icon that reveals the field on click. Text still goes through
/// `sendTVText` / Android TV Remote IME — same path as Damion’s Fling, not ADB.
struct RemoteView: View {
    @ObservedObject var state: AppState
    @State private var typed = ""
    @State private var showTypeField = false
    @FocusState private var typeFocused: Bool
    @ObservedObject private var capture = KeyCapture.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if capture.on && !typeFocused && (state.tvPaired || state.panel == .casting) {
                keyboardStrip
            }
            switch state.tvPairing {
            case .starting:
                HStack(spacing: 7) {
                    ProgressView().controlSize(.small)
                    Text("Contacting TV…").font(.system(size: 11.5)).foregroundStyle(.secondary)
                }
                .padding(.horizontal, 14).padding(.vertical, 10)
            case .waitingForPIN, .verifying:
                TVPinEntry(state: state)
            case .idle, .failed:
                if state.tvPaired { controls } else { setup }
                if case .failed(let why) = state.tvPairing {
                    Text(why)
                        .font(.system(size: 11)).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, 14).padding(.bottom, 7)
                }
            }
        }
    }

    /// Says so while the Mac keyboard goes to the TV instead of the app the user was in.
    private var keyboardStrip: some View {
        Text("⌨  Keyboard captured — Esc to exit")
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(Color.accentColor)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 8).padding(.vertical, 5)
            .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 6))
            .padding(.horizontal, 10).padding(.top, 8)
    }

    // MARK: - unpaired

    private var setup: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Pair once with the TV to unlock apps, the d-pad, and typing.")
                .font(.system(size: 11)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 14).padding(.top, 10).padding(.bottom, 6)
            MenuRow(title: "Turn TV On", icon: "power") { Task { await state.wakeTV() } }
            MenuRow(title: "Set Up TV Power…", icon: "dot.radiowaves.left.and.right",
                    accented: true) {
                Task { await state.startTVPairing() }
            }
        }
        .padding(.bottom, 4)
    }

    // MARK: - paired

    private var controls: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            appGrid
            dpad
            volumeKeys
            typeRow
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                SectionLabel(text: "ON THE TV")
                if let subtitle = headerSubtitle {
                    Text(subtitle)
                        .font(.system(size: 12.5, weight: .semibold))
                        .lineLimit(1)
                }
            }
            Spacer()
            KeyButton(symbol: "power", size: 26, state: state, code: ATVKeyCode.power)
                .help(powerHelp)
        }
        .padding(.horizontal, 14).padding(.top, 10).padding(.bottom, 8)
    }

    private var headerSubtitle: String? {
        if let package = state.tvCurrentApp { return "Now: \(TVApp.displayName(forPackage: package))" }
        if state.tvIsOn == false { return "TV is off" }
        return nil
    }

    private var powerHelp: String {
        switch state.tvIsOn {
        case true?:  return "Turn TV off"
        case false?: return "Turn TV on"
        case nil:    return "Toggle TV power"
        }
    }

    private var appGrid: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 4), count: 4),
                  spacing: 4) {
            ForEach(TVApp.catalog) { app in
                AppChip(name: app.name, active: app.package == state.tvCurrentApp) {
                    Task { await state.launchTVApp(app) }
                }
            }
        }
        .padding(.horizontal, 12).padding(.bottom, 8)
    }

    /// Classic remote layout — Back and Home flank the OK row.
    private var dpad: some View {
        VStack(spacing: 6) {
            KeyButton(symbol: "chevron.up", size: 30, state: state, code: ATVKeyCode.dpadUp)
            HStack(spacing: 13) {
                KeyButton(symbol: "arrow.uturn.backward", size: 30,
                          state: state, code: ATVKeyCode.back)
                    .help("Back")
                KeyButton(symbol: "chevron.left", size: 30, state: state, code: ATVKeyCode.dpadLeft)
                KeyButton(label: "OK", size: 40, accented: true,
                          state: state, code: ATVKeyCode.dpadCenter)
                KeyButton(symbol: "chevron.right", size: 30, state: state, code: ATVKeyCode.dpadRight)
                KeyButton(symbol: "house", size: 30, state: state, code: ATVKeyCode.home)
                    .help("Home")
            }
            KeyButton(symbol: "chevron.down", size: 30, state: state, code: ATVKeyCode.dpadDown)
            Text("arrow keys, or scroll anywhere, to move around the TV")
                .font(.system(size: 9.5)).foregroundStyle(.quaternary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 6).padding(.bottom, 8)
    }

    private var volumeKeys: some View {
        HStack(spacing: 5) {
            KeyPill(symbol: "speaker.minus", label: "Vol −",
                    state: state, code: ATVKeyCode.volumeDown)
            KeyPill(symbol: "speaker.slash", label: "Mute",
                    state: state, code: ATVKeyCode.volumeMute)
            KeyPill(symbol: "speaker.plus", label: "Vol +",
                    state: state, code: ATVKeyCode.volumeUp)
        }
        .padding(.horizontal, 12).padding(.top, 8).padding(.bottom, 4)
    }

    /// Keyboard icon reveals the type field; mic stays beside it.
    /// Transport unchanged: `AppState.sendTVText` → Android TV Remote IME.
    private var typeRow: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Button {
                    withAnimation(.easeInOut(duration: 0.15)) {
                        showTypeField.toggle()
                    }
                    if showTypeField {
                        DispatchQueue.main.async { typeFocused = true }
                    } else {
                        typed = ""
                        typeFocused = false
                    }
                } label: {
                    Image(systemName: "keyboard")
                        .font(.system(size: 11.5, weight: .medium))
                        .foregroundStyle(Color.primary)
                }
                .buttonStyle(.plain)
                .frame(width: 24, height: 22)
                .background(showTypeField ? Color.accentColor.opacity(0.22)
                                          : Color.primary.opacity(0.07),
                            in: RoundedRectangle(cornerRadius: 5))
                .help(showTypeField ? "Hide keyboard" : "Keyboard — type on the TV")
                .accessibilityLabel(showTypeField ? "Hide keyboard" : "Show keyboard")

                if showTypeField {
                    TextField("Type on TV", text: $typed)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12))
                        .focused($typeFocused)
                        .onSubmit { sendTyped() }
                        .help("Lands in whatever text field is focused on the TV")
                        .transition(.opacity.combined(with: .move(edge: .leading)))
                }

                Button {
                    Task { await state.toggleVoiceSearch() }
                } label: {
                    Image(systemName: state.tvVoiceActive ? "mic.fill" : "mic")
                        .font(.system(size: 11.5))
                        .foregroundStyle(state.tvVoiceActive ? Color.red : Color.primary)
                }
                .buttonStyle(.plain)
                .frame(width: 24, height: 22)
                .background(state.tvVoiceActive ? Color.red.opacity(0.15)
                                                : Color.primary.opacity(0.07),
                            in: RoundedRectangle(cornerRadius: 5))
                .help(state.tvVoiceActive ? "Stop voice search" : "Voice search")
            }
            if state.tvVoiceActive {
                Text("Listening — click the mic again when done.")
                    .font(.system(size: 10)).foregroundStyle(.red)
            }
        }
        .padding(.horizontal, 14).padding(.top, 4).padding(.bottom, 9)
        .onChange(of: state.tvPaired) { _, paired in
            if !paired {
                showTypeField = false
                typed = ""
                typeFocused = false
            }
        }
    }

    private func sendTyped() {
        let text = typed
        typed = ""
        guard !text.isEmpty else { return }
        Task { await state.sendTVText(text) }
    }
}

// MARK: - controls

/// Round icon key for the d-pad cluster and power.
struct KeyButton: View {
    var symbol: String? = nil
    var label: String? = nil
    var size: CGFloat = 32
    var accented = false
    @ObservedObject var state: AppState
    let code: Int32

    @State private var hovering = false
    @State private var flash = false   // pressed on the Mac keyboard (KeyRemote)

    var body: some View {
        Button {
            Task { await state.pressTVKey(code) }
        } label: {
            Group {
                if let symbol {
                    Image(systemName: symbol).font(.system(size: size * 0.36, weight: .semibold))
                } else {
                    Text(label ?? "").font(.system(size: size * 0.3, weight: .semibold))
                }
            }
            .foregroundStyle(accented ? Color.accentColor : Color.primary)
            .frame(width: size, height: size)
            .background(
                Circle().fill(flash ? Color.accentColor.opacity(0.35)
                              : hovering ? Color.accentColor.opacity(0.22)
                                         : Color.primary.opacity(accented ? 0.1 : 0.07))
            )
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .onReceive(NotificationCenter.default.publisher(for: RemoteKeyFlash.name)) { note in
            guard note.userInfo?["code"] as? Int32 == code else { return }
            flash = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) { flash = false }
        }
    }
}

/// Wide pill key: Back/Home and the volume cluster.
struct KeyPill: View {
    let symbol: String
    let label: String
    @ObservedObject var state: AppState
    let code: Int32

    @State private var hovering = false

    var body: some View {
        Button {
            Task { await state.pressTVKey(code) }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: symbol).font(.system(size: 10))
                Text(label).font(.system(size: 11))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 5)
            .background(hovering ? Color.accentColor.opacity(0.2) : Color.primary.opacity(0.06),
                        in: RoundedRectangle(cornerRadius: 5))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

/// App launcher chip; `active` marks the app the TV reports as foreground.
struct AppChip: View {
    let name: String
    var active = false
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Text(name)
                .font(.system(size: 10.5, weight: active ? .semibold : .regular))
                .foregroundStyle(active ? Color.accentColor : Color.primary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 5)
                .background(active ? Color.accentColor.opacity(0.18)
                            : hovering ? Color.accentColor.opacity(0.2)
                                       : Color.primary.opacity(0.06),
                            in: RoundedRectangle(cornerRadius: 5))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}
