import AppKit
import FlingKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: StatusItemController?
    private var shortcuts: GlobalShortcuts?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Port (macOS guard): self check, launch checks, watchdog — before anything runs
        GuardBootstrap.start()
        let catt = CattClient.resolveExecutable().map {
            CattClient(executable: $0, runner: SystemProcessRunner())
        }
        // With voice negotiated, KEYCODE_SEARCH puts the TV into mic-listening
        // mode; typed search still works through IME text.
        let state = AppState(catt: catt, browsers: BrowserReader(),
                             atv: AndroidTVRemote(enableVoice: true),
                             prober: TabProber())
        controller = StatusItemController(state: state)
        shortcuts = GlobalShortcuts(state: state)
        // Port: extension bridge, ⌃⌥⌘C, file queue, notifications, Settings (PortServices.swift)
        PortServices.shared.start(state: state)
        ADBGlue.install(state: state)       // port: ADB (ADBView.swift)
    }

    /// Port: an APK opened with Fling from Finder ("Open With") is installed on the TV.
    func application(_ application: NSApplication, open urls: [URL]) {
        let apks = urls.filter { $0.pathExtension.lowercased() == "apk" }
        if !apks.isEmpty { ADBGlue.install(apks) }
    }

    /// Port: stops Fling's own adb server only (Android Studio's is left alone).
    func applicationWillTerminate(_ notification: Notification) {
        ADBGlue.shutdown()
    }
}
