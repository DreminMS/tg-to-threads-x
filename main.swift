import Cocoa
import SQLite3

class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    var timer: Timer?
    var gitTimer: Timer?
    var isSyncingGit = false
    let dbPath = "/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads/crosspost.db"
    let projectDir = "/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads"
    
    struct Stats {
        var published: Int = 0
        var pending: Int = 0
        var failed: Int = 0
        var total: Int { published + pending + failed }
        var nextId: Int? = nil
        var nextDate: String = ""
        var nextPhotoCount: Int = 0
        var percent: Double {
            total > 0 ? (Double(published) / Double(total) * 100.0) : 0.0
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.title = "🧵 ..."
        }
        
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        
        updateUI()
        
        // Refresh local UI every 4 seconds
        timer = Timer.scheduledTimer(withTimeInterval: 4.0, repeats: true) { [weak self] _ in
            self?.updateUI()
        }
        
        // Auto-sync database from GitHub Actions every 60 seconds in the background
        gitTimer = Timer.scheduledTimer(withTimeInterval: 60.0, repeats: true) { [weak self] _ in
            self?.syncWithGitHub(showLoading: false)
        }
        
        // Initial sync on start
        syncWithGitHub(showLoading: false)
    }
    
    func menuWillOpen(_ menu: NSMenu) {
        rebuildMenu()
        // Pull latest updates when user clicks status bar icon
        syncWithGitHub(showLoading: false)
    }
    
    func isRealtimeServiceRunning() -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        process.arguments = ["-f", "crosspost.py --daemon"]
        try? process.run()
        process.waitUntilExit()
        return process.terminationStatus == 0
    }

    func fetchStats() -> Stats {
        var stats = Stats()
        var db: OpaquePointer?
        if sqlite3_open_v2(dbPath, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK {
            let query = """
            SELECT 
              (SELECT count(*) FROM archive_posts WHERE status = 'published'),
              (SELECT count(*) FROM archive_posts WHERE status = 'pending'),
              (SELECT count(*) FROM archive_posts WHERE status = 'failed'),
              (SELECT post_id FROM archive_posts WHERE status = 'pending' ORDER BY post_id ASC LIMIT 1),
              (SELECT post_date FROM archive_posts WHERE status = 'pending' ORDER BY post_id ASC LIMIT 1),
              (SELECT photos_json FROM archive_posts WHERE status = 'pending' ORDER BY post_id ASC LIMIT 1)
            ;
            """
            var stmt: OpaquePointer?
            if sqlite3_prepare_v2(db, query, -1, &stmt, nil) == SQLITE_OK {
                if sqlite3_step(stmt) == SQLITE_ROW {
                    stats.published = Int(sqlite3_column_int(stmt, 0))
                    stats.pending = Int(sqlite3_column_int(stmt, 1))
                    stats.failed = Int(sqlite3_column_int(stmt, 2))
                    if sqlite3_column_type(stmt, 3) != SQLITE_NULL {
                        stats.nextId = Int(sqlite3_column_int(stmt, 3))
                    }
                    if let d = sqlite3_column_text(stmt, 4) {
                        stats.nextDate = String(cString: d)
                    }
                    if let j = sqlite3_column_text(stmt, 5) {
                        let jsonStr = String(cString: j)
                        if let data = jsonStr.data(using: .utf8),
                           let arr = try? JSONSerialization.jsonObject(with: data) as? [Any] {
                            stats.nextPhotoCount = arr.count
                        }
                    }
                }
                sqlite3_finalize(stmt)
            }
            sqlite3_close(db)
        }
        return stats
    }
    
    func isTwitterConfigured() -> Bool {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: "\(projectDir)/config.json")),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let enabled = json["twitter_enabled"] as? Bool,
              let cookies = json["twitter_cookies"] as? [String: String] else {
            return false
        }
        return enabled && !(cookies["auth_token"] ?? "").isEmpty
    }

    func updateUI() {
        let stats = fetchStats()
        let isRtRunning = isRealtimeServiceRunning()
        let twConfigured = isTwitterConfigured()
        
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let button = self.statusItem.button else { return }
            if self.isSyncingGit {
                button.title = "🧵 🔄 ..."
                return
            }
            let twIcon = twConfigured ? " | 𝕏" : ""
            let rtIcon = isRtRunning ? "" : " ⚠️"
            if stats.total > 0 {
                button.title = "🧵\(twIcon) \(stats.published)/\(stats.total)\(rtIcon)"
            } else {
                button.title = "🧵\(twIcon) Poster\(rtIcon)"
            }
        }
    }
    
    func rebuildMenu() {
        guard let menu = statusItem.menu else { return }
        menu.removeAllItems()
        
        let stats = fetchStats()
        let isRtRunning = isRealtimeServiceRunning()
        let twConfigured = isTwitterConfigured()
        
        // Header
        let headerItem = NSMenuItem(title: "🧵 Threads & 𝕏 Auto-Poster", action: nil, keyEquivalent: "")
        let titleAttr: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: 14)
        ]
        headerItem.attributedTitle = NSAttributedString(string: "🧵 Threads & 𝕏 Auto-Poster", attributes: titleAttr)
        headerItem.isEnabled = false
        menu.addItem(headerItem)
        
        // Status rows
        let cloudStr = "☁️ Архив на GitHub Actions: АКТИВЕН (каждый час)"
        let cloudItem = NSMenuItem(title: cloudStr, action: nil, keyEquivalent: "")
        cloudItem.isEnabled = false
        menu.addItem(cloudItem)

        let rtStr = isRtRunning ? "🟢 TG ➔ Threads & 𝕏 кросспост: АКТИВЕН (локально)" : "⏸️ TG ➔ Threads & 𝕏 кросспост: НЕ ЗАПУЩЕН"
        let rtItem = NSMenuItem(title: rtStr, action: nil, keyEquivalent: "")
        rtItem.isEnabled = false
        menu.addItem(rtItem)

        let twStr = twConfigured ? "🟢 Twitter (X) интеграция: ПОДКЛЮЧЕНА" : "⚪ Twitter (X) интеграция: ОЖИДАЕТ КУКИ"
        let twItem = NSMenuItem(title: twStr, action: nil, keyEquivalent: "")
        twItem.isEnabled = false
        menu.addItem(twItem)
        
        menu.addItem(NSMenuItem.separator())
        
        // Progress statistics
        let progressStr = String(format: "📊 Опубликовано: %d из %d (%.1f%%)", stats.published, stats.total, stats.percent)
        let progItem = NSMenuItem(title: progressStr, action: nil, keyEquivalent: "")
        progItem.isEnabled = false
        menu.addItem(progItem)
        
        let daysLeft = Double(stats.pending) / 24.0
        let leftStr = String(format: "⏳ В очереди: %d постов (~%.1f дней)", stats.pending, daysLeft)
        let leftItem = NSMenuItem(title: leftStr, action: nil, keyEquivalent: "")
        leftItem.isEnabled = false
        menu.addItem(leftItem)
        
        if let nextId = stats.nextId {
            let cleanDate = stats.nextDate.prefix(10)
            let nextStr = "📸 Следующий: #\(nextId) (\(cleanDate), \(stats.nextPhotoCount) фото)"
            let nextItem = NSMenuItem(title: nextStr, action: nil, keyEquivalent: "")
            nextItem.isEnabled = false
            menu.addItem(nextItem)
        }
        
        menu.addItem(NSMenuItem.separator())
        
        // Cloud & Local Actions
        let gitSyncItem = NSMenuItem(title: "🔄 Синхронизировать с GitHub прямо сейчас", action: #selector(manualGitSync), keyEquivalent: "g")
        gitSyncItem.target = self
        menu.addItem(gitSyncItem)

        let triggerActionsItem = NSMenuItem(title: "⚡ Опубликовать 1 пост в облаке (GitHub Actions)", action: #selector(openGitHubActions), keyEquivalent: "")
        triggerActionsItem.target = self
        menu.addItem(triggerActionsItem)

        let publishLocalItem = NSMenuItem(title: "⚡ Опубликовать 1 пост прямо на этом Mac", action: #selector(publishLocal), keyEquivalent: "p")
        publishLocalItem.target = self
        menu.addItem(publishLocalItem)
        
        let syncTgItem = NSMenuItem(title: "🔄 Синхронизировать архив из Telegram", action: #selector(syncArchiveFromTelegram), keyEquivalent: "s")
        syncTgItem.target = self
        menu.addItem(syncTgItem)
        
        menu.addItem(NSMenuItem.separator())
        
        // Quick Links & Logs
        let ghActionsLink = NSMenuItem(title: "🌐 Открыть страницу GitHub Actions", action: #selector(openGitHubActions), keyEquivalent: "")
        ghActionsLink.target = self
        menu.addItem(ghActionsLink)

        let threadsLink = NSMenuItem(title: "🌐 Открыть профиль Threads (@m_dremin)", action: #selector(openThreadsProfile), keyEquivalent: "")
        threadsLink.target = self
        menu.addItem(threadsLink)

        let twitterLink = NSMenuItem(title: "🌐 Открыть профиль Twitter/X (@m_dremin)", action: #selector(openTwitterProfile), keyEquivalent: "")
        twitterLink.target = self
        menu.addItem(twitterLink)
        
        let rtLogItem = NSMenuItem(title: "📋 Открыть лог реалтайма (stdout.log)", action: #selector(openRtLog), keyEquivalent: "")
        rtLogItem.target = self
        menu.addItem(rtLogItem)
        
        let folderItem = NSMenuItem(title: "📂 Открыть папку скрипта в Finder", action: #selector(openFolder), keyEquivalent: "")
        folderItem.target = self
        menu.addItem(folderItem)
        
        menu.addItem(NSMenuItem.separator())
        
        // Quit
        let quitItem = NSMenuItem(title: "✕ Закрыть статусбар", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
    }
    
    @objc func manualGitSync() {
        syncWithGitHub(showLoading: true)
    }
    
    func syncWithGitHub(showLoading: Bool) {
        if isSyncingGit { return }
        isSyncingGit = true
        if showLoading, let button = statusItem.button {
            button.title = "🧵 🔄 ..."
        }
        
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            p.arguments = ["pull", "--rebase", "origin", "main"]
            p.currentDirectoryURL = URL(fileURLWithPath: self.projectDir)
            try? p.run()
            p.waitUntilExit()
            
            DispatchQueue.main.async {
                self.isSyncingGit = false
                self.updateUI()
                if let menu = self.statusItem.menu, menu.numberOfItems > 0 {
                    self.rebuildMenu()
                }
            }
        }
    }
    
    @objc func publishLocal() {
        if let button = statusItem.button {
            button.title = "🧵 Публикую..."
        }
        executeShellAsync("/opt/homebrew/bin/python3 ./archive_poster.py --publish-one")
    }
    
    @objc func syncArchiveFromTelegram() {
        if let button = statusItem.button {
            button.title = "🧵 Синхр TG..."
        }
        executeShellAsync("/opt/homebrew/bin/python3 ./archive_poster.py --sync")
    }
    
    @objc func openGitHubActions() {
        if let url = URL(string: "https://github.com/DreminMS/tg-to-threads-x/actions/workflows/archive_poster.yml") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openThreadsProfile() {
        if let url = URL(string: "https://www.threads.net/@m_dremin") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openTwitterProfile() {
        if let url = URL(string: "https://x.com/m_dremin") {
            NSWorkspace.shared.open(url)
        }
    }
    
    @objc func openRtLog() {
        NSWorkspace.shared.open(URL(fileURLWithPath: "\(projectDir)/stdout.log"))
    }
    
    @objc func openFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: projectDir))
    }
    
    @objc func quitApp() {
        NSApplication.shared.terminate(nil)
    }
    
    func executeShellAsync(_ command: String) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/bash")
            p.arguments = ["-c", command]
            p.currentDirectoryURL = URL(fileURLWithPath: self.projectDir)
            try? p.run()
            p.waitUntilExit()
            
            // Push changes if any to GitHub so cloud stays in sync
            let pushP = Process()
            pushP.executableURL = URL(fileURLWithPath: "/bin/bash")
            pushP.arguments = ["-c", "git add crosspost.db && git diff --quiet && git diff --staged --quiet || (git commit -m 'Sync local progress' && git push origin main)"]
            pushP.currentDirectoryURL = URL(fileURLWithPath: self.projectDir)
            try? pushP.run()
            pushP.waitUntilExit()
            
            DispatchQueue.main.async {
                self.updateUI()
                if let menu = self.statusItem.menu, menu.numberOfItems > 0 {
                    self.rebuildMenu()
                }
            }
        }
    }
}

// Entry Point
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
