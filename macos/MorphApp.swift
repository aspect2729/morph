// Morph — native macOS shell.
// Runs the local conversion engine (FastAPI on 127.0.0.1:7788) as a child process,
// shows the UI in a native WebKit window, saves downloads to ~/Downloads,
// and stops the engine when the app quits.
import Cocoa
import WebKit

let PORT = 7788
let BASE = URL(string: "http://127.0.0.1:\(PORT)")!

func morphDir() -> URL {
    if let p = Bundle.main.object(forInfoDictionaryKey: "MorphDirectory") as? String {
        return URL(fileURLWithPath: (p as NSString).expandingTildeInPath)
    }
    return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Morph")
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var loading: NSTextField!

    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()
        buildWindow()
        NSApp.activate(ignoringOtherApps: true)
        if healthy() { load(); return }
        startServer()
    }

    // MARK: engine

    func healthy() -> Bool {
        var req = URLRequest(url: BASE.appendingPathComponent("api/health"))
        req.timeoutInterval = 0.5
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, r, _ in
            ok = (r as? HTTPURLResponse)?.statusCode == 200
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 1)
        return ok
    }

    func startServer() {
        let dir = morphDir()
        let py = dir.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: py.path) else {
            fail("Morph's engine isn't installed at \(dir.path).\nRun setup.sh in that folder, then reopen Morph.")
            return
        }
        let p = Process()
        p.executableURL = py
        p.arguments = ["-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", "\(PORT)"]
        p.currentDirectoryURL = dir
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        p.environment = env
        let logURL = dir.appendingPathComponent(".morph.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let log = try? FileHandle(forWritingTo: logURL) {
            log.truncateFile(atOffset: 0)
            p.standardOutput = log
            p.standardError = log
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self, self.server === proc, !self.quitting else { return }
                self.fail("The conversion engine stopped unexpectedly.\nSee \(logURL.path) for details.")
            }
        }
        do { try p.run() } catch { fail("Couldn't start the engine: \(error.localizedDescription)"); return }
        server = p
        DispatchQueue.global().async {
            for _ in 0..<120 {   // up to 30s
                if self.healthy() { DispatchQueue.main.async { self.load() }; return }
                if !p.isRunning { return }
                Thread.sleep(forTimeInterval: 0.25)
            }
            DispatchQueue.main.async { self.fail("The engine took too long to start. See \(logURL.path).") }
        }
    }

    var quitting = false
    func applicationWillTerminate(_ n: Notification) {
        quitting = true
        if let p = server, p.isRunning {
            p.terminate()
            let deadline = Date().addingTimeInterval(3)
            while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    // MARK: window

    func buildWindow() {
        let cfg = WKWebViewConfiguration()
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        web = WKWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = self
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        web.isHidden = true

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Morph"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = .black
        window.appearance = NSAppearance(named: .darkAqua)
        window.minSize = NSSize(width: 520, height: 480)
        window.setFrameAutosaveName("MorphMain")
        if !window.setFrameUsingName("MorphMain") { window.center() }

        let root = NSView()
        root.wantsLayer = true
        root.layer?.backgroundColor = NSColor.black.cgColor
        loading = NSTextField(labelWithString: "STARTING ENGINE ·")
        loading.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        loading.textColor = NSColor(white: 0.5, alpha: 1)
        loading.alignment = .center
        loading.translatesAutoresizingMaskIntoConstraints = false
        web.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(web)
        root.addSubview(loading)
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            web.topAnchor.constraint(equalTo: root.topAnchor),
            web.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            loading.centerXAnchor.constraint(equalTo: root.centerXAnchor),
            loading.centerYAnchor.constraint(equalTo: root.centerYAnchor),
            loading.widthAnchor.constraint(lessThanOrEqualToConstant: 480),
        ])
        window.contentView = root
        window.makeKeyAndOrderFront(nil)
    }

    func load() {
        web.load(URLRequest(url: BASE))
    }

    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        loading.isHidden = true
        web.isHidden = false
    }

    func fail(_ msg: String) {
        loading.stringValue = msg
        loading.textColor = NSColor(red: 1, green: 0.23, blue: 0.19, alpha: 1)
        loading.isHidden = false
        web.isHidden = true
    }

    // file picker for <input type=file>
    func webView(_ w: WKWebView, runOpenPanelWith p: WKOpenPanelParameters,
                 initiatedByFrame f: WKFrameInfo, completionHandler done: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = p.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.beginSheetModal(for: window) { r in done(r == .OK ? panel.urls : nil) }
    }

    // MARK: downloads -> ~/Downloads

    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if a.shouldPerformDownload || a.request.url?.path.hasPrefix("/api/download/") == true {
            decisionHandler(.download); return
        }
        if let u = a.request.url, u.host != "127.0.0.1" {   // external links open in the browser
            NSWorkspace.shared.open(u); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    func webView(_ w: WKWebView, decidePolicyFor r: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(r.canShowMIMEType ? .allow : .download)
    }

    func webView(_ w: WKWebView, navigationAction a: WKNavigationAction, didBecome d: WKDownload) { d.delegate = self }
    func webView(_ w: WKWebView, navigationResponse r: WKNavigationResponse, didBecome d: WKDownload) { d.delegate = self }

    var pending: [ObjectIdentifier: URL] = [:]

    func download(_ d: WKDownload, decideDestinationUsing r: URLResponse, suggestedFilename name: String,
                  completionHandler done: @escaping (URL?) -> Void) {
        let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var dst = dir.appendingPathComponent(name)
        let stem = dst.deletingPathExtension().lastPathComponent, ext = dst.pathExtension
        var i = 2
        while FileManager.default.fileExists(atPath: dst.path) {
            dst = dir.appendingPathComponent(ext.isEmpty ? "\(stem) \(i)" : "\(stem) \(i).\(ext)"); i += 1
        }
        pending[ObjectIdentifier(d)] = dst
        done(dst)
    }

    func downloadDidFinish(_ d: WKDownload) {
        guard let dst = pending.removeValue(forKey: ObjectIdentifier(d)) else { return }
        // bounce the Dock's Downloads stack, then show the file
        DistributedNotificationCenter.default().post(name: .init("com.apple.DownloadFileFinished"), object: dst.path)
        NSWorkspace.shared.activateFileViewerSelecting([dst])
    }

    func download(_ d: WKDownload, didFailWithError e: Error, resumeData: Data?) {
        pending.removeValue(forKey: ObjectIdentifier(d))
        let a = NSAlert(); a.messageText = "Download failed"; a.informativeText = e.localizedDescription
        a.beginSheetModal(for: window)
    }

    // MARK: menu

    func buildMenu() {
        let main = NSMenu()
        func item(_ m: NSMenu, _ t: String, _ s: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) {
            let i = NSMenuItem(title: t, action: s, keyEquivalent: k); i.keyEquivalentModifierMask = mods; m.addItem(i)
        }
        let appM = NSMenu()
        item(appM, "About Morph", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), "")
        appM.addItem(.separator())
        item(appM, "Hide Morph", #selector(NSApplication.hide(_:)), "h")
        appM.addItem(.separator())
        item(appM, "Quit Morph", #selector(NSApplication.terminate(_:)), "q")
        let fileM = NSMenu(title: "File")
        item(fileM, "Open Files…", #selector(openFiles), "o")
        item(fileM, "Save", #selector(jsSave), "s")
        item(fileM, "Close Window", #selector(NSWindow.performClose(_:)), "w")
        let editM = NSMenu(title: "Edit")
        item(editM, "Undo", #selector(jsUndo), "z")
        item(editM, "Redo", #selector(jsRedo), "z", [.command, .shift])
        editM.addItem(.separator())
        item(editM, "Cut", #selector(NSText.cut(_:)), "x")
        item(editM, "Copy", #selector(NSText.copy(_:)), "c")
        item(editM, "Paste", #selector(NSText.paste(_:)), "v")
        item(editM, "Select All", #selector(NSText.selectAll(_:)), "a")
        let viewM = NSMenu(title: "View")
        item(viewM, "Reload", #selector(reload), "r")
        item(viewM, "Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])
        let winM = NSMenu(title: "Window")
        item(winM, "Minimize", #selector(NSWindow.performMiniaturize(_:)), "m")
        item(winM, "Zoom", #selector(NSWindow.performZoom(_:)), "")
        for (title, m) in [("Morph", appM), ("File", fileM), ("Edit", editM), ("View", viewM), ("Window", winM)] {
            let top = NSMenuItem(title: title, action: nil, keyEquivalent: ""); top.submenu = m; main.addItem(top)
        }
        NSApp.mainMenu = main
        NSApp.windowsMenu = winM
    }

    @objc func reload() { web.reload() }
    @objc func openFiles() { web.evaluateJavaScript("window.morphOpen ? morphOpen() : document.getElementById('file').click()") }
    @objc func jsUndo() { web.evaluateJavaScript("window.morphKey && morphKey('undo')") }
    @objc func jsRedo() { web.evaluateJavaScript("window.morphKey && morphKey('redo')") }
    @objc func jsSave() { web.evaluateJavaScript("window.morphKey && morphKey('save')") }

    // JavaScript alert() / confirm() inside the web view
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo,
                 completionHandler done: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.beginSheetModal(for: window) { _ in done() }
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo,
                 completionHandler done: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in done(r == .alertFirstButtonReturn) }
    }

    // files dropped on the Dock icon / "Open With"
    func application(_ app: NSApplication, open urls: [URL]) {
        pendingOpen += urls
        flushOpen()
    }
    var pendingOpen: [URL] = []
    func flushOpen() {
        guard !pendingOpen.isEmpty, web != nil, !web.isHidden else {
            if !pendingOpen.isEmpty { DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.flushOpen() } }
            return
        }
        let urls = pendingOpen; pendingOpen = []
        // hand files to the page as File objects
        for u in urls {
            let size = (try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            if size > 400_000_000 {   // very large: ask the user to drag it into the window instead
                let a = NSAlert(); a.messageText = "\(u.lastPathComponent) is large"
                a.informativeText = "Drag it straight into the Morph window to convert it."
                a.beginSheetModal(for: window); continue
            }
            guard let data = try? Data(contentsOf: u) else { continue }
            let b64 = data.base64EncodedString()
            let name = u.lastPathComponent.replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "'", with: "\\'")
            web.evaluateJavaScript("""
              (async()=>{const r=await fetch('data:application/octet-stream;base64,\(b64)');
              add([new File([await r.blob()],'\(name)')]);})()
            """)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
