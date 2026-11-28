// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
// Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
import AppKit
import FlingKit

/// Port (macOS guard): wires FlingKit's guard to the app — the self check at start,
/// the "trust this program?" question, the warnings. See SECURITY.md →
/// "What protects you after the install (macOS)".
enum GuardBootstrap {
    static let watchdog = GuardWatchdog()

    @MainActor static func start() {
        let check = SelfCheck.run()
        let g = ProcessGuard.shared
        g.mode = check.mode
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        g.log.info("Fling Port \(version) started: mode=\(check.mode.rawValue) signer=\(check.signer) " +
                   "hardenedRuntime=\(check.hardenedRuntime) receipt=" +
                   (check.receipt.map { "verified=\($0.verified) version=\($0.version ?? "?")" } ?? "none"))
        if check.tampered {
            for d in check.details { g.log.alert("self check: \(d)") }
            if check.mode == .enforce {
                showTampered(check.problems)
                exit(1)
            }
            g.log.warn("report-only build: self-check problems are only logged")
        }
        g.askToTrust = { request in GuardPrompt.ask(request) }
        g.onEvent = { event in DispatchQueue.main.async { GuardAlerts.show(event) } }
        watchdog.start()
    }

    @MainActor private static func showTampered(_ problems: [String]) {
        let a = NSAlert()
        a.alertStyle = .critical
        a.messageText = tr("Fling Port has been changed")
        a.informativeText = String(format: tr("Fling Port is not the copy that was installed: %@.\n\nReinstall it with the install script (see SECURITY.md). Fling Port will now quit."),
                                   problems.map { tr($0) }.joined(separator: "; "))
        a.addButton(withTitle: tr("Quit"))
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }
}

/// "Allow Fling Port to run …?" — asked once per program and version, from whatever
/// thread is about to start it.
enum GuardPrompt {
    static func ask(_ request: ExecutablePolicy.TrustRequest) -> Bool {
        if Thread.isMainThread { return MainActor.assumeIsolated { present(request) } }
        return DispatchQueue.main.sync { MainActor.assumeIsolated { present(request) } }
    }

    @MainActor private static func present(_ r: ExecutablePolicy.TrustRequest) -> Bool {
        let a = NSAlert()
        a.alertStyle = .warning
        let name = r.displayName
        if r.isChange {
            a.messageText = String(format: tr("“%@” has changed. Allow the new version?"), name)
            a.informativeText = tr("Fling Port remembers the exact contents of the programs you allowed. This one is different now — normal after an update (brew upgrade, uv tool upgrade). Allow it only if you updated it yourself.")
        } else {
            a.messageText = String(format: tr("Allow Fling Port to run “%@”?"), name)
            a.informativeText = tr("This program is not signed by Apple or a known developer, so Fling Port checks it by its exact contents. Allow it only if you installed it yourself (for example with Homebrew or uv).")
        }
        let files = r.items.map { item -> String in
            let kind = item.kind == .script ? tr("script") : item.kind == .interpreter ? tr("interpreter") : tr("program")
            return "\(kind): \(item.path)\nSHA-256 \(item.sha256.prefix(16))…" +
                (item.isChange ? " (" + tr("changed") + ")" : "")
        }.joined(separator: "\n")
        a.informativeText += "\n\n" + files
        a.addButton(withTitle: tr("Allow"))
        a.addButton(withTitle: tr("Don't Allow"))
        NSApp.activate(ignoringOtherApps: true)
        return a.runModal() == .alertFirstButtonReturn
    }
}

/// Warnings after the guard refused or stopped something (one per program per run).
enum GuardAlerts {
    @MainActor private static var shown = Set<String>()

    @MainActor static func show(_ event: GuardEvent) {
        let a = NSAlert()
        a.alertStyle = .warning
        let key: String
        switch event {
        case .refused(let exe, let reason):
            key = "refused:\(exe)"
            // you just answered "Don't Allow" yourself: no second dialog
            if reason == ProcessGuard.declinedReason || reason == ProcessGuard.busyReason { return }
            a.messageText = tr("Fling Port stopped a program")
            a.informativeText = String(format: tr("Fling Port did not start %@: %@."), exe, tr(reason))
        case .stopped(_, let exe, let reason):
            key = "stopped:\(exe)"
            a.messageText = tr("Fling Port stopped a program")
            switch reason {
            case .cpu:
                a.informativeText = String(format: tr("%@ kept more than 30%% of the processor busy for 90 seconds."), exe)
            case .unchecked(let why):
                a.informativeText = String(format: tr("%@ was started by one of Fling Port's programs and did not pass the check: %@."), exe, tr(why))
            }
        }
        guard shown.insert(key).inserted else { return }
        a.informativeText += "\n\n" + tr("Details: ~/Library/Logs/Fling Port/guard.log")
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }
}

/// Russian for the guard's phrases (looked up by `tr()` after its own table).
enum GuardL10n {
    static let ru: [String: String] = [
        "Fling Port has been changed": "Fling Port изменён",
        "Fling Port is not the copy that was installed: %@.\n\nReinstall it with the install script (see SECURITY.md). Fling Port will now quit.":
            "Fling Port — не та копия, что была установлена: %@.\n\nПереустановите его установочным скриптом (см. SECURITY.md). Сейчас Fling Port закроется.",
        "Quit": "Выйти",
        "“%@” has changed. Allow the new version?": "«%@» изменился. Разрешить новую версию?",
        "Fling Port remembers the exact contents of the programs you allowed. This one is different now — normal after an update (brew upgrade, uv tool upgrade). Allow it only if you updated it yourself.":
            "Fling Port запоминает точное содержимое разрешённых программ. Эта теперь другая — так бывает после обновления (brew upgrade, uv tool upgrade). Разрешайте, только если обновляли её сами.",
        "Allow Fling Port to run “%@”?": "Разрешить Fling Port запускать «%@»?",
        "This program is not signed by Apple or a known developer, so Fling Port checks it by its exact contents. Allow it only if you installed it yourself (for example with Homebrew or uv).":
            "Эта программа не подписана Apple или известным разработчиком, поэтому Fling Port проверяет её по точному содержимому. Разрешайте, только если устанавливали её сами (например, через Homebrew или uv).",
        "script": "скрипт",
        "interpreter": "интерпретатор",
        "program": "программа",
        "changed": "изменён",
        "Allow": "Разрешить",
        "Don't Allow": "Не разрешать",
        "Fling Port stopped a program": "Fling Port остановил программу",
        "Fling Port did not start %@: %@.": "Fling Port не запустил %@: %@.",
        "%@ kept more than 30%% of the processor busy for 90 seconds.":
            "%@ занимал больше 30%% процессора 90 секунд подряд.",
        "%@ was started by one of Fling Port's programs and did not pass the check: %@.":
            "%@ запущен одной из программ Fling Port и не прошёл проверку: %@.",
        "Details: ~/Library/Logs/Fling Port/guard.log": "Подробности: ~/Library/Logs/Fling Port/guard.log",
        // reasons (FlingKit: ExecutablePolicy.Reason, SelfCheck.Problem)
        "it is not given by its full path": "путь к ней указан не полностью",
        "the file does not exist": "файла нет",
        "it is not an executable file": "это не исполняемый файл",
        "it is neither a program nor a script": "это не программа и не скрипт",
        "an unsigned program in a temporary folder": "неподписанная программа во временной папке",
        "the script names no interpreter Fling can check": "скрипт ссылается на интерпретатор, который Fling не может проверить",
        "the script's interpreter is itself a script": "интерпретатор скрипта — сам скрипт",
        "you did not allow it": "вы её не разрешили",
        "another question about a program is still open": "ещё открыт другой вопрос о программе",
        "not trusted": "она не разрешена",
        "its files were changed after it was installed": "его файлы изменены после установки",
        "it is not signed": "он не подписан",
        "it is signed by someone else": "он подписан кем-то другим",
        "the program is not the one that was installed": "программа — не та, что была установлена",
        "its install record can't be read": "запись об установке не читается",
        "its install record is missing": "нет записи об установке",
    ]
}
