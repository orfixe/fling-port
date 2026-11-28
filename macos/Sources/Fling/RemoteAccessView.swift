// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import SwiftUI
import FlingKit

// Port: "Remote access (Tailscale)" — TVs on the tailnet with Add, adding by address,
// what works away from home and the security note (desktop: TailnetSection and the
// Settings page in fling_tailscale.py). `RemoteAccessView` can be embedded in a Settings
// window; `RemoteAccessWindow.show(state:)` opens it on its own.

@MainActor
final class TailnetModel: ObservableObject {
    @Published private(set) var state: TailnetState?
    @Published private(set) var loading = false
    @Published var showAll = false
    @Published var address = ""
    @Published private(set) var adding: String?
    @Published private(set) var message: String?

    func refresh() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        state = await Task.detached(priority: .userInitiated) { () -> TailnetState in
            guard let cli = TailscaleCLI.find() else { return .notInstalled }
            return cli.state()
        }.value
    }

    func add(_ text: String, name: String? = nil, into app: AppState) async {
        let text = text.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty, adding == nil else { return }
        message = nil
        adding = text
        defer { adding = nil }
        let status = state?.status
        do {
            let (ip, host) = try await Task.detached { try TailnetProbe.resolve(text, status: status) }.value
            adding = ip
            let tv = try await TailnetProbe.probe(ip: ip, name: name ?? host)
            await app.addTailnetTV(tv)
            if address == text { address = "" }
            if !tv.cast {
                message = "Added. Its Cast port doesn't answer over Tailscale, so casting won't work — the remote does."
            }
        } catch {
            message = (error as? TailnetError)?.description ?? error.localizedDescription
        }
    }
}

struct RemoteAccessView: View {
    @ObservedObject var app: AppState
    @StateObject private var model = TailnetModel()
    @State private var copied = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                card { statusBlock }
                if let s = model.state?.status { card { peersBlock(s) } }
                card { addBlock }
                section("What works away from home") { card { worksBlock } }
                section("Security") { card { securityBlock } }
                Text(tr("Fling only reads the Tailscale status and never changes its settings."))
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            .padding(18)
        }
        .frame(minWidth: 440, idealWidth: 460, minHeight: 420, idealHeight: 640)
        .task { await model.refresh() }
    }

    // MARK: - pieces

    private var header: some View {
        HStack {
            Text(tr("Remote access (Tailscale)")).font(.system(size: 16, weight: .semibold))
            Spacer()
            Button { Task { await model.refresh() } } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .disabled(model.loading)
            .help(tr("Refresh"))
        }
    }

    @ViewBuilder private var statusBlock: some View {
        switch model.state {
        case nil:
            HStack(spacing: 8) { ProgressView().controlSize(.small); note("Checking Tailscale…") }
        case .notInstalled?:
            title("Tailscale isn't installed on this Mac.")
            note("With Tailscale, the remote works away from home. Install it on this Mac and on the TV, and sign in to the same account on both.")
            links(download: true)
        case .unreadable(let detail)?:
            title("Couldn't read the Tailscale status.")
            Text(detail).font(.system(size: 10.5, design: .monospaced)).foregroundStyle(.secondary)
                .textSelection(.enabled)
        case .off(let backend)?:
            title(backend == "NeedsLogin" || backend == "NeedsMachineAuth"
                  ? "You aren't signed in to Tailscale."
                  : "Tailscale is off.")
            note("Turn it on and sign in from the Tailscale icon in the menu bar, then press Refresh. Fling doesn't switch Tailscale on by itself.")
            links(download: false)
        case .running(let s)?:
            title("Tailscale is on")
            if let me = s.me {
                note("This Mac: " + [me.host, me.ip].filter { !$0.isEmpty }.joined(separator: " · "))
            }
            if !s.login.isEmpty { note("Account: " + s.login) }
        }
    }

    private func peersBlock(_ s: TailnetStatus) -> some View {
        let known = Set(app.devices.map(\.ip))
        let tvs = s.peers.filter { $0.looksLikeTV || known.contains($0.ip) }
        let shown = model.showAll ? s.peers : tvs
        return VStack(alignment: .leading, spacing: 8) {
            title("TVs on your tailnet")
            if shown.isEmpty {
                note("There are no TVs on your tailnet. Install Tailscale on the TV (Google Play) and sign in to the same account as on this Mac.")
            }
            ForEach(shown) { peer in
                peerRow(peer, added: known.contains(peer.ip), selected: app.selectedDevice?.ip == peer.ip)
            }
            if s.peers.count > tvs.count {
                Button(tr(model.showAll ? "TVs only" : "Show all devices (\(s.peers.count))")) {
                    model.showAll.toggle()
                }
                .buttonStyle(.link).font(.system(size: 11.5))
            }
            ForEach(s.subnetRouters) { r in
                note("Home network through \(r.host): \(r.routes.joined(separator: ", ")). Add a TV there by its home address below.")
            }
        }
    }

    private func peerRow(_ p: TailnetPeer, added: Bool, selected: Bool) -> some View {
        HStack(spacing: 10) {
            Circle().fill(p.online ? Color.green : Color.secondary.opacity(0.4)).frame(width: 7, height: 7)
                .help(tr(p.online ? "online" : "offline"))
            VStack(alignment: .leading, spacing: 1) {
                Text(p.host).font(.system(size: 12.5, weight: .semibold)).lineLimit(1)
                    .help(p.dns.isEmpty ? p.host : p.dns)
                Text(([p.ip] + (p.short.isEmpty || p.short.lowercased() == p.host.lowercased() ? [] : [p.short])
                      + [tr(p.online ? "online" : "offline")]).joined(separator: " · "))
                    .font(.system(size: 10.5)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if selected {
                Text(tr("selected")).font(.system(size: 11)).foregroundStyle(Color.accentColor)
            } else if added {
                Button(tr("Select")) {
                    if let d = app.devices.first(where: { $0.ip == p.ip }) { Task { await app.select(device: d) } }
                }
                .controlSize(.small)
            } else {
                Button(tr("Add")) { Task { await model.add(p.ip, name: p.host, into: app) } }
                    .controlSize(.small)
                    .disabled(!p.online || model.adding != nil)
                    .help(tr(p.online ? "Add this TV and control it over Tailscale"
                                      : "The TV isn't on the tailnet right now"))
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
    }

    private var addBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            title("Add a TV by address")
            HStack(spacing: 8) {
                TextField(tr("100.x.y.z or MagicDNS name"), text: $model.address)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await model.add(model.address, into: app) } }
                Button(tr("Add")) { Task { await model.add(model.address, into: app) } }
                    .disabled(model.address.trimmingCharacters(in: .whitespaces).isEmpty || model.adding != nil)
            }
            if let ip = model.adding {
                HStack(spacing: 6) { ProgressView().controlSize(.small); note("Checking \(ip) over Tailscale…") }
            } else if let m = model.message {
                Text(tr(m)).font(.system(size: 11)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            let saved = TailnetTVStore.shared.all
            if !saved.isEmpty {
                Divider()
                ForEach(saved, id: \.ip) { tv in
                    HStack {
                        Text(tv.name).font(.system(size: 12)).lineLimit(1)
                        Text(tv.ip).font(.system(size: 10.5)).foregroundStyle(.secondary)
                        if !tv.cast {
                            Text(tr("no casting")).font(.system(size: 10.5)).foregroundStyle(.orange)
                        }
                        Spacer()
                        Button(tr("Remove")) { app.removeTailnetTV(ip: tv.ip) }
                            .buttonStyle(.link).font(.system(size: 11))
                    }
                }
            }
        }
    }

    private var worksBlock: some View {
        VStack(alignment: .leading, spacing: 7) {
            check(true, "The remote, TV volume, apps, typing and voice search (the TV's ports 6466/6467)")
            check(true, "ADB and the TV's live screen, where this build has them: they connect by address too")
            check(nil, "Casting tabs and links (catt) needs the home network: away from home it works only if the TV answers on the Cast port (8009), which most don't")
            check(false, "Finding TVs on the network (mDNS stays at home): add the TV by its Tailscale address")
        }
    }

    private var securityBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            title("ADB over Tailscale is full access to the TV")
            note("In your Tailscale access rules (ACLs), let only your own devices reach the TV's ports 5555, 6466 and 6467. Fling opens nothing to the internet and forwards no ports.")
            note("Tag the TV as tag:tv in the Tailscale admin console (Machines → the TV → Edit ACL tags) and paste the rule below. If your rules allow everything to everyone (src \"*\" → dst \"*:*\"), remove that rule, or every device on the tailnet can reach the TV.")
            Text(Tailnet.aclExample(login: model.state?.status?.login ?? ""))
                .font(.system(size: 10.5, design: .monospaced))
                .textSelection(.enabled)
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
            Button(tr(copied ? "Copied" : "Copy an example ACL")) {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(Tailnet.aclExample(login: model.state?.status?.login ?? ""),
                                               forType: .string)
                copied = true
                Task { try? await Task.sleep(for: .seconds(1.6)); copied = false }
            }
            .controlSize(.small)
        }
    }

    // MARK: - building blocks

    private func links(download: Bool) -> some View {
        HStack(spacing: 14) {
            if download { Link(tr("Download Tailscale"), destination: Tailnet.downloadURL) }
            Link(tr("How it works"), destination: Tailnet.docsURL(russian: Lang.isRussian))
        }
        .font(.system(size: 11.5))
    }

    private func check(_ ok: Bool?, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: ok == true ? "checkmark.circle.fill" : ok == nil ? "minus.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(ok == true ? Color.green : ok == nil ? Color.orange : Color.secondary)
            Text(tr(text)).font(.system(size: 12))
                .foregroundStyle(ok == false ? .secondary : .primary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func title(_ s: String) -> some View {
        Text(tr(s)).font(.system(size: 12.5, weight: .semibold))
    }

    private func note(_ s: String) -> some View {
        Text(tr(s)).font(.system(size: 11.5)).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func card<C: View>(@ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 6) { content() }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
    }

    private func section<C: View>(_ s: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(tr(s)).font(.system(size: 13, weight: .semibold))
            content()
        }
    }
}

/// "via Tailscale · 38 ms" beside the TV's name in the footer, while a tailnet TV is selected.
struct TailnetBadge: View {
    @ObservedObject var state: AppState
    @ObservedObject private var monitor = TailnetLinkMonitor.shared

    var body: some View {
        Group {
            if state.selectedDevice?.isTailnet == true {
                Text(label).font(.system(size: 10.5))
                    .foregroundStyle(monitor.link.online == false ? Color.orange : Color.secondary)
                    .lineLimit(1)
                    .help(tip)
            }
        }
        .onAppear { monitor.follow(state.selectedDevice) }
        .onChange(of: state.selectedDevice) { _, d in monitor.follow(d) }
        .onDisappear { monitor.stop() }
    }

    private var label: String {
        if monitor.link.online == false { return tr("via Tailscale · no connection") }
        if let ms = monitor.link.ping?.ms { return tr("via Tailscale · \(Int(ms.rounded())) ms") }
        return tr("via Tailscale")
    }

    private var tip: String {
        if monitor.link.online == false {
            return tr("The TV isn't answering over Tailscale. The remote connects again by itself once the connection is back.")
        }
        guard let p = monitor.link.ping else { return tr("Tailscale: checking the connection to the TV…") }
        if !p.relay.isEmpty {
            return tr("Tailscale: through the \(p.relay.uppercased()) relay, so the delay is higher than with a direct connection.")
        }
        return tr(p.direct == true ? "Tailscale: a direct connection to the TV." : "Tailscale: the TV answers.")
    }
}

/// The Remote access window. Fling is a menu-bar app (no main menu), so the window
/// handles ⌘V / ⌘C / ⌘X / ⌘A for its address field itself.
@MainActor
enum RemoteAccessWindow {
    private static var window: NSWindow?

    static func show(state: AppState) {
        if window == nil {
            let w = EditingWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 640),
                                  styleMask: [.titled, .closable, .resizable, .miniaturizable],
                                  backing: .buffered, defer: false)
            w.title = tr("Remote access (Tailscale)")
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: RemoteAccessView(app: state))
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    private final class EditingWindow: NSWindow {
        override func performKeyEquivalent(with event: NSEvent) -> Bool {
            if event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
               let key = event.charactersIgnoringModifiers {
                let action: Selector? = switch key {
                case "v": #selector(NSText.paste(_:))
                case "c": #selector(NSText.copy(_:))
                case "x": #selector(NSText.cut(_:))
                case "a": #selector(NSText.selectAll(_:))
                case "w": #selector(NSWindow.performClose(_:))
                default: nil
                }
                if let action, NSApp.sendAction(action, to: nil, from: self) { return true }
            }
            return super.performKeyEquivalent(with: event)
        }
    }
}
