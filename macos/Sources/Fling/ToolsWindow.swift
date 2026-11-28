// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
//
// Port: the "Tools" window of the Windows / Linux build (desktop/fling_tools.py,
// desktop/fling_catalog.py) for macOS. Two tabs:
//   Tools   — catt, adb and scrcpy: every copy found on the Mac with its version, the
//             latest one, and Install / Update through Homebrew (uv for catt) on a click,
//             with the command's output shown; plain instructions when that can't be done.
//   Catalog — official free TV apps (VLC, Kodi, Jellyfin…), verified before they're
//             installed on the TV over ADB.
import AppKit
import FlingKit
import SwiftUI

enum ToolsTab: String { case tools, catalog }

/// "{name}" style templates: translate the template, then fill it in.
func trf(_ template: String, _ values: [String: String]) -> String {
    var s = tr(template)
    for (k, v) in values { s = s.replacingOccurrences(of: "{\(k)}", with: v) }
    return s
}

@MainActor
final class ToolsWindowController: NSObject, NSWindowDelegate {
    static let shared = ToolsWindowController()
    private var window: NSWindow?
    private let selection = TabSelection()
    private var tools: ToolsModel?
    private var catalog: CatalogModel?

    final class TabSelection: ObservableObject { @Published var tab: ToolsTab = .tools }

    func show(state: AppState, tab: ToolsTab = .tools) {
        selection.tab = tab
        if window == nil {
            let tools = ToolsModel(), catalog = CatalogModel(state: state)
            self.tools = tools; self.catalog = catalog
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 620, height: 700),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable],
                             backing: .buffered, defer: false)
            w.title = tr("Fling — tools")
            w.isReleasedWhenClosed = false
            w.contentMinSize = NSSize(width: 480, height: 420)
            w.contentViewController = NSHostingController(rootView:
                ToolsRootView(selection: selection, tools: tools, catalog: catalog))
            w.delegate = self
            w.center()
            window = w
            tools.refresh()
            catalog.refresh()
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

struct ToolsRootView: View {
    @ObservedObject var selection: ToolsWindowController.TabSelection
    @ObservedObject var tools: ToolsModel
    @ObservedObject var catalog: CatalogModel

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $selection.tab) {
                Text(tr("Tools")).tag(ToolsTab.tools)
                Text(tr("Catalog")).tag(ToolsTab.catalog)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 280)
            .padding(.vertical, 10)
            Divider()
            ScrollView {
                Group {
                    if selection.tab == .tools { ToolsPage(model: tools) } else { CatalogPage(model: catalog) }
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .frame(minWidth: 480, minHeight: 420)
    }
}

// MARK: - shared bits

struct Card<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 6) { content }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color.primary.opacity(0.045)))
    }
}

struct LinkRow: View {
    let links: [(String, String)]
    var body: some View {
        HStack(spacing: 12) {
            ForEach(Array(links.enumerated()), id: \.offset) { _, l in
                if let u = URL(string: l.1) { Link(l.0, destination: u).font(.caption) }
            }
        }
    }
}

func mb(_ n: Int) -> String { String(format: "%.1f", Double(n) / 1_048_576) }

// MARK: - Tools tab

@MainActor
final class ToolsModel: ObservableObject {
    struct Row {
        var copies: [ToolCopy] = []
        var scanning = false
        var latest: String?
        var checkingLatest = false
        var running: StreamingProcess?
        var log = ""
        var exitCode: Int32?
    }

    @Published var rows: [String: Row] = [:]
    @Published var brew: String?
    @Published var uv: String?
    private let http = URLSessionCatalogHTTP()

    func row(_ key: String) -> Row { rows[key] ?? Row() }

    func refresh(latest: Bool = true) {
        brew = ToolLocator.brew
        uv = ToolLocator.uv
        for spec in ToolLocator.tools {
            rows[spec.key, default: Row()].scanning = true
            Task.detached(priority: .userInitiated) {
                let copies = ToolLocator.scan(spec)
                await MainActor.run {
                    self.rows[spec.key, default: Row()].copies = copies
                    self.rows[spec.key, default: Row()].scanning = false
                }
            }
            if latest {
                rows[spec.key, default: Row()].checkingLatest = true
                let http = self.http
                Task {
                    let v = await ToolLocator.latestVersion(spec, http: http)
                    self.rows[spec.key, default: Row()].latest = v
                    self.rows[spec.key, default: Row()].checkingLatest = false
                }
            }
        }
    }

    func inUse(_ key: String) -> ToolCopy? { row(key).copies.first { $0.compatible } ?? row(key).copies.first }

    func command(_ spec: ToolSpec) -> ToolCommand? {
        ToolLocator.command(for: spec, have: inUse(spec.key), brew: brew, uv: uv)
    }

    func run(_ spec: ToolSpec) {
        guard let cmd = ToolLocator.command(for: spec, have: inUse(spec.key), brew: brew, uv: uv),
              row(spec.key).running == nil else { return }
        let p = StreamingProcess()
        rows[spec.key, default: Row()].running = p
        rows[spec.key, default: Row()].log = "$ \(cmd.display)\n"
        rows[spec.key, default: Row()].exitCode = nil
        Task {
            let code = await p.run(cmd) { text in
                Task { @MainActor in
                    var log = self.rows[spec.key, default: Row()].log + text
                    if log.count > 40_000 { log = String(log.suffix(40_000)) }
                    self.rows[spec.key, default: Row()].log = log
                }
            }
            try? await Task.sleep(nanoseconds: 100_000_000)
            self.rows[spec.key, default: Row()].running = nil
            self.rows[spec.key, default: Row()].exitCode = code
            self.refresh(latest: false)
        }
    }

    func cancel(_ key: String) { row(key).running?.cancel() }
}

struct ToolsPage: View {
    @ObservedObject var model: ToolsModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(tr("Programs Fling uses for casting, ADB and the TV screen. If they're already on this Mac, Fling finds them; if not, it can install them with Homebrew (catt with uv)."))
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Text(model.brew != nil ? trf("Homebrew: {path}", ["path": model.brew!]) : tr("Homebrew isn't installed."))
                    .font(.caption).foregroundStyle(.secondary)
                if model.brew == nil, let u = URL(string: "https://brew.sh") {
                    Link(tr("How to install Homebrew"), destination: u).font(.caption)
                }
                Spacer()
                Button(tr("Search again")) { model.refresh() }
            }
            ForEach(ToolLocator.tools) { spec in ToolCard(model: model, spec: spec) }
        }
    }
}

struct ToolCard: View {
    @ObservedObject var model: ToolsModel
    let spec: ToolSpec

    var body: some View {
        let row = model.row(spec.key)
        let use = model.inUse(spec.key)
        Card {
            HStack {
                Text(spec.title).font(.headline)
                Spacer()
                status(row, use)
            }
            Text(tr(spec.purpose)).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if row.scanning && row.copies.isEmpty {
                HStack(spacing: 6) { ProgressView().controlSize(.small); Text(tr("Searching this Mac…")).font(.caption) }
            } else if row.copies.isEmpty {
                Text(tr("Not found on this Mac.")).font(.callout)
            } else {
                ForEach(row.copies) { c in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: c == use ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(c == use ? Color.accentColor : Color.secondary).font(.caption)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(c.path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                            Text(copyLine(c)).font(.caption2).foregroundStyle(c.compatible ? Color.secondary : Color.orange)
                        }
                    }
                }
            }
            latestLine(row, use)
            actions(row, use)
            if !row.log.isEmpty {
                ScrollViewReader { proxy in
                    ScrollView {
                        Text(row.log).font(.system(size: 10.5, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled).id("end")
                    }
                    .frame(height: 130)
                    .padding(6)
                    .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.06)))
                    .onChange(of: row.log) { proxy.scrollTo("end", anchor: .bottom) }
                }
            }
            if let code = row.exitCode {
                Text(code == 0 ? tr("Done.") : trf("It didn't work (exit code {code}).", ["code": String(code)]))
                    .font(.caption).foregroundStyle(code == 0 ? Color.green : Color.red)
            }
            LinkRow(links: [(tr("Licence"), spec.licenseURL), (tr("Project website"), spec.homepage)])
        }
    }

    @ViewBuilder func status(_ row: ToolsModel.Row, _ use: ToolCopy?) -> some View {
        if row.running != nil { Text(tr("Installing…")).font(.caption).foregroundStyle(.blue) }
        else if use == nil, !row.scanning { Text(tr("Not found")).font(.caption).foregroundStyle(.red) }
        else if let use, !use.compatible { Text(tr("Outdated")).font(.caption).foregroundStyle(.orange) }
        else if use != nil { Text(tr("Found")).font(.caption).foregroundStyle(.green) }
    }

    func copyLine(_ c: ToolCopy) -> String {
        let src = tr(["brew": "Homebrew", "uv": "uv", "sdk": "Android SDK", "path": "PATH"][c.source] ?? c.source)
        let v = c.version.map { trf("version {v}", ["v": $0]) } ?? tr("version ?")
        var s = "\(v) · \(src)"
        if !c.compatible { s += " · " + trf("Version {v} or newer is needed.", ["v": spec.minVersion]) }
        return s
    }

    @ViewBuilder func latestLine(_ row: ToolsModel.Row, _ use: ToolCopy?) -> some View {
        if row.checkingLatest {
            Text(tr("Checking the latest version…")).font(.caption).foregroundStyle(.secondary)
        } else if let l = row.latest {
            if ToolVersion.isNewer(l, than: use?.version) {
                Text(trf("{v} available", ["v": l])).font(.caption).foregroundStyle(.blue)
            } else if use?.version != nil {
                Text(trf("Latest version {v} — the latest version", ["v": l])).font(.caption).foregroundStyle(.secondary)
            } else {
                Text(trf("Latest version {v}", ["v": l])).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder func actions(_ row: ToolsModel.Row, _ use: ToolCopy?) -> some View {
        let cmd = model.command(spec)
        let outdated = use.map { !$0.compatible || ToolVersion.isNewer(row.latest, than: $0.version) } ?? false
        if row.running != nil {
            HStack { ProgressView().controlSize(.small); Button(tr("Cancel")) { model.cancel(spec.key) } }
        } else if use == nil || outdated {
            if let cmd {
                HStack {
                    Button(use == nil ? tr("Install") : (row.latest.map { trf("Update to {v}", ["v": $0]) } ?? tr("Update"))) {
                        model.run(spec)
                    }
                    .buttonStyle(.borderedProminent)
                    Text(trf("Runs: {cmd}", ["cmd": cmd.display])).font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary).lineLimit(2)
                }
            } else {
                Text(tr(spec.manual)).font(.caption).fixedSize(horizontal: false, vertical: true)
                if spec.key == "adb", let u = URL(string: spec.homepage) {
                    Link(tr("Google's page"), destination: u).font(.caption)
                } else if spec.key == "scrcpy", let u = URL(string: "https://github.com/Genymobile/scrcpy/releases") {
                    Link(tr("scrcpy releases"), destination: u).font(.caption)
                }
            }
        } else if use?.source == "brew" || use?.source == "uv", let cmd, cmd.arguments.contains("upgrade") {
            Button(tr("Check for an update")) { model.run(spec) }.controlSize(.small)
        }
    }
}

// MARK: - Catalog tab

@MainActor
final class CatalogModel: ObservableObject {
    struct Job {
        var step: CatalogStep?
        var message: String?            // English template, translated on display
        var values: [String: String] = [:]
        var failed = false
        var running: Bool { step != nil }
    }

    let state: AppState
    @Published var tv = TvInfo()
    @Published var probing = false
    @Published var latest: [String: Release] = [:]
    @Published var latestError: [String: String] = [:]
    @Published var jobs: [String: Job] = [:]
    @Published var connecting = false
    @Published var adbError: String?
    private let installer = CatalogInstaller()
    private let peeks = CatalogPeekCache()

    init(state: AppState) { self.state = state }

    func refresh(fresh: Bool = false) {
        let d = state.selectedDevice
        var base = TvInfo(ip: d?.ip ?? "", name: d?.name ?? "")
        guard !base.ip.isEmpty else { tv = base; loadLatest(fresh: fresh); return }
        probing = true
        let installer = self.installer
        Task {
            if await installer.adb.ready(ip: base.ip, connect: false) == nil { base.adb = true; self.adbError = nil }
            let info = await installer.probe(base)
            self.tv = info
            self.probing = false
            self.loadLatest(fresh: fresh)
        }
    }

    func connectADB() {
        guard !tv.ip.isEmpty else { return }
        connecting = true
        let ip = tv.ip, installer = self.installer
        Task {
            let err = await installer.adb.ready(ip: ip, connect: true)
            self.connecting = false
            self.adbError = err
            if err == nil { self.refresh() }
        }
    }

    private func loadLatest(fresh: Bool) {
        let abis = tv.abis.isEmpty ? Catalog.defaultABIs : tv.abis
        for app in Catalog.apps {
            let peeks = self.peeks
            Task {
                do {
                    self.latest[app.key] = try await peeks.peek(app, abis: abis, fresh: fresh)
                    self.latestError[app.key] = nil
                } catch {
                    self.latestError[app.key] = (error as? CatalogError)?.message ?? error.localizedDescription
                }
            }
        }
    }

    func install(_ app: CatalogApp) {
        guard !(jobs[app.key]?.running ?? false) else { return }
        jobs[app.key] = Job(step: .meta)
        let ip = tv.ip, installer = self.installer
        Task {
            do {
                let v = try await installer.install(app, ip: ip) { step in
                    Task { @MainActor in if self.jobs[app.key]?.running == true { self.jobs[app.key]?.step = step } }
                }
                self.jobs[app.key] = Job(message: CatalogInstaller.installedMessage, values: ["name": app.title, "version": v])
            } catch is CancellationError {
                self.jobs[app.key] = Job(message: "Cancelled.")
            } catch {
                self.jobs[app.key] = Job(message: (error as? CatalogError)?.message ?? error.localizedDescription, failed: true)
            }
            self.refresh()
        }
    }

    func remove(_ app: CatalogApp) {
        let a = NSAlert()
        a.messageText = trf("Remove {name} from the TV?", ["name": app.title])
        a.informativeText = tr("The app's settings and data on the TV are removed too.")
        a.addButton(withTitle: tr("Remove"))
        a.addButton(withTitle: tr("Cancel"))
        guard a.runModal() == .alertFirstButtonReturn else { return }
        jobs[app.key] = Job(step: .tvRemove)
        let ip = tv.ip, installer = self.installer
        Task {
            do {
                try await installer.remove(app, ip: ip) { _ in }
                self.jobs[app.key] = Job(message: CatalogInstaller.removedMessage, values: ["name": app.title])
            } catch {
                self.jobs[app.key] = Job(message: (error as? CatalogError)?.message ?? error.localizedDescription, failed: true)
            }
            self.refresh()
        }
    }

    func open(_ app: CatalogApp) {
        jobs[app.key] = Job(message: "Opening on the TV…")
        let ip = tv.ip, installer = self.installer
        Task { await installer.open(app, ip: ip) }
    }
}

struct CatalogPage: View {
    @ObservedObject var model: CatalogModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(tr("Extra modules, from official sources only. Before installing, Fling checks every file against the publisher's checksum or signed catalogue, and for TV apps also the developer's signing key."))
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack(alignment: .firstTextBaseline) {
                Text(tr("TV apps")).font(.title3.bold())
                Spacer()
                Button(tr("Refresh")) { model.refresh(fresh: true) }
            }
            tvLine
            ForEach(Catalog.apps) { app in AppCard(model: model, app: app) }
        }
    }

    @ViewBuilder var tvLine: some View {
        let tv = model.tv
        if tv.ip.isEmpty {
            Text(tr("No TV chosen — TV apps can be installed once Fling finds a TV.")).font(.callout)
        } else if model.probing {
            HStack(spacing: 6) { ProgressView().controlSize(.small); Text(trf("{name}: checking ADB…", ["name": tv.name])) }
        } else if !tv.adb {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(trf("{name}: ADB isn't connected. TV apps are installed over ADB.", ["name": tv.name]))
                    Button(model.connecting ? tr("Connecting…") : tr("Connect ADB")) { model.connectADB() }
                        .disabled(model.connecting)
                }
                if let e = model.adbError { Text(tr(e)).font(.caption).foregroundStyle(.red) }
            }
        } else {
            let bits = [trf("{name} · ADB connected", ["name": tv.name]),
                        tv.android.isEmpty ? nil : trf("Android {v}", ["v": tv.android]),
                        tv.abis.first.map { trf("processor {abi}", ["abi": $0]) }].compactMap { $0 }
            Text(bits.joined(separator: " · ")).font(.callout).foregroundStyle(.green)
            if !tv.error.isEmpty { Text(tr(tv.error)).font(.caption).foregroundStyle(.red) }
        }
    }
}

struct AppCard: View {
    @ObservedObject var model: CatalogModel
    let app: CatalogApp

    var body: some View {
        let job = model.jobs[app.key]
        let onTV = model.tv.installed[app.pkg]
        let rel = model.latest[app.key]
        Card {
            HStack(spacing: 10) {
                Text(app.tile.letters).font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
                    .frame(width: 30, height: 30)
                    .background(RoundedRectangle(cornerRadius: 7).fill(Color(hex: app.tile.color)))
                Text(app.title).font(.headline)
                Spacer()
                if onTV != nil { Text(tr("On the TV")).font(.caption).foregroundStyle(.green) }
            }
            Text(tr(app.purpose)).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            infoLine(onTV, rel)
            if let job, let step = job.step {
                HStack(spacing: 6) { ProgressView().controlSize(.small); Text(stepText(step)).font(.caption) }
                if case .download(let d, let t) = step, t > 0 { ProgressView(value: Double(d), total: Double(t)) }
                if case .index(let d, let t) = step, t > 0 { ProgressView(value: Double(d), total: Double(t)) }
            } else if let job, let m = job.message {
                Text(job.failed ? tr("Failed: ") + tr(m) : trf(m, job.values)).font(.caption)
                    .foregroundStyle(job.failed ? Color.red : Color.green).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                let canInstall = model.tv.adb && !(job?.running ?? false)
                if onTV == nil {
                    Button(tr("Install on TV")) { model.install(app) }.disabled(!canInstall).buttonStyle(.borderedProminent)
                } else {
                    if let rel, let v = onTV, ToolVersion.isNewer(shortVersion(rel.version), than: v) {
                        Button(trf("Update to {v}", ["v": shortVersion(rel.version)])) { model.install(app) }
                            .disabled(!canInstall).buttonStyle(.borderedProminent)
                    }
                    Button(tr("Open")) { model.open(app) }.disabled(!canInstall)
                    Button(tr("Remove from TV")) { model.remove(app) }.disabled(!canInstall)
                }
                Spacer()
            }
            .controlSize(.small)
            LinkRow(links: [(tr("Source:") + " " + app.source.name, app.sourceURL), (tr("Licence"), app.licenseURL),
                            (tr("Project website"), app.homepage)])
            if !model.tv.adb, !model.tv.ip.isEmpty, job == nil {
                Text(tr("Connect ADB first: the “Connect ADB” button at the top.")).font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    func infoLine(_ onTV: String?, _ rel: Release?) -> Text {
        var parts: [String] = []
        if let v = onTV { parts.append(v.isEmpty ? tr("On the TV: version unknown") : trf("On the TV: version {v}", ["v": shortVersion(v)])) }
        if let rel {
            var s = trf("Latest version {v}", ["v": shortVersion(rel.version)])
            if !rel.abi.isEmpty { s += trf(" for {abi}", ["abi": rel.abi]) }
            if rel.size > 0 { s += " · \(mb(rel.size)) \(tr("MB"))" }
            parts.append(s)
        } else if let e = model.latestError[app.key] {
            parts.append(tr("Couldn't check the version: ") + tr(e))
        }
        return Text(parts.joined(separator: " · ")).font(.caption)
    }

    func stepText(_ s: CatalogStep) -> String {
        switch s {
        case .meta: return tr("Checking the latest version…")
        case .index(let d, let t): return trf("Loading the F-Droid catalogue: {done} of {total} MB", ["done": mb(d), "total": mb(t)])
        case .download(let d, let t):
            return t > 0 ? trf("Downloading: {done} of {total} MB", ["done": mb(d), "total": mb(t)])
                         : trf("Downloading: {done} MB", ["done": mb(d)])
        case .verify: return tr("Verifying the checksum…")
        case .sign: return tr("Checking the app's signature…")
        case .tv: return tr("Installing on the TV — this can take a couple of minutes…")
        case .tvCheck: return tr("Checking that the app is on the TV…")
        case .tvRemove: return tr("Removing from the TV…")
        }
    }
}

extension Color {
    init(hex: String) {
        let v = UInt32(hex.trimmingCharacters(in: CharacterSet(charactersIn: "#")), radix: 16) ?? 0x888888
        self.init(red: Double(v >> 16 & 0xFF) / 255, green: Double(v >> 8 & 0xFF) / 255, blue: Double(v & 0xFF) / 255)
    }
}
