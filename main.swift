import Cocoa
import SQLite3

class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    var timer: Timer?
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
        
        // Refresh every 4 seconds
        timer = Timer.scheduledTimer(withTimeInterval: 4.0, repeats: true) { [weak self] _ in
            self?.updateUI()
        }
    }
    
    func menuWillOpen(_ menu: NSMenu) {
        rebuildMenu()
    }
    
    func isArchiveServiceRunning() -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        process.arguments = ["-f", "archive_poster.py --daemon"]
        try? process.run()
        process.waitUntilExit()
        return process.terminationStatus == 0
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
              let key = json["twitter_consumer_key"] as? String else {
            return false
        }
        return enabled && !key.contains("REPLACE")
    }

    func updateUI() {
        let stats = fetchStats()
        let isRunning = isArchiveServiceRunning()
        let twConfigured = isTwitterConfigured()
        
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let button = self.statusItem.button else { return }
            let stateIcon = isRunning ? "" : " ⏸"
            let twIcon = twConfigured ? " | 𝕏" : ""
            if stats.total > 0 {
                button.title = "🧵\(twIcon) \(stats.published)/\(stats.total)\(stateIcon)"
            } else {
                button.title = "🧵\(twIcon) Poster\(stateIcon)"
            }
        }
    }
    
    func rebuildMenu() {
        guard let menu = statusItem.menu else { return }
        menu.removeAllItems()
        
        let stats = fetchStats()
        let isArchiveRunning = isArchiveServiceRunning()
        let isRtRunning = isRealtimeServiceRunning()
        let twConfigured = isTwitterConfigured()
        
        // Header
        let headerItem = NSMenuItem(title: "🧵 Threads & 𝕏 Poster", action: nil, keyEquivalent: "")
        let titleAttr: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: 14)
        ]
        headerItem.attributedTitle = NSAttributedString(string: "🧵 Threads & 𝕏 Auto-Poster", attributes: titleAttr)
        headerItem.isEnabled = false
        menu.addItem(headerItem)
        
        // Status row
        let statusStr = isArchiveRunning ? "🟢 Автопостинг архива (раз в час): АКТИВЕН" : "⏸️ Автопостинг архива: НА ПАУЗЕ"
        let statusItem = NSMenuItem(title: statusStr, action: nil, keyEquivalent: "")
        statusItem.isEnabled = false
        menu.addItem(statusItem)

        let rtStr = isRtRunning ? "🟢 TG ➔ Threads кросспост: АКТИВЕН" : "⏸️ TG ➔ Threads кросспост: НЕ ЗАПУЩЕН"
        let rtItem = NSMenuItem(title: rtStr, action: nil, keyEquivalent: "")
        rtItem.isEnabled = false
        menu.addItem(rtItem)

        let twStr = twConfigured ? "🟢 Twitter (X) интеграция: ПОДКЛЮЧЕНА" : "⚪ Twitter (X) интеграция: ОЖИДАЕТ КЛЮЧЕЙ"
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
        
        // Actions
        if isArchiveRunning {
            let pauseItem = NSMenuItem(title: "⏸️ Приостановить автопостинг", action: #selector(toggleArchiveService), keyEquivalent: "")
            pauseItem.target = self
            menu.addItem(pauseItem)
        } else {
            let startItem = NSMenuItem(title: "▶️ Запустить автопостинг (раз в час)", action: #selector(toggleArchiveService), keyEquivalent: "")
            startItem.target = self
            menu.addItem(startItem)
        }
        
        let publishNowItem = NSMenuItem(title: "⚡ Опубликовать 1 пост прямо сейчас", action: #selector(publishNow), keyEquivalent: "p")
        publishNowItem.target = self
        menu.addItem(publishNowItem)
        
        let syncItem = NSMenuItem(title: "🔄 Синхронизировать архив из Telegram", action: #selector(syncArchive), keyEquivalent: "s")
        syncItem.target = self
        menu.addItem(syncItem)
        
        menu.addItem(NSMenuItem.separator())
        
        // Logs & Folders
        let logItem = NSMenuItem(title: "📋 Открыть лог архива (archive_stdout.log)", action: #selector(openArchiveLog), keyEquivalent: "")
        logItem.target = self
        menu.addItem(logItem)
        
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
    
    @objc func toggleArchiveService() {
        let isRunning = isArchiveServiceRunning()
        let script = isRunning ? "./stop_archive_service.sh" : "./setup_archive_service.sh"
        executeShellAsync(script)
    }
    
    @objc func publishNow() {
        if let button = statusItem.button {
            button.title = "🧵 Публикую..."
        }
        executeShellAsync("./archive_poster.py --publish-one")
    }
    
    @objc func syncArchive() {
        if let button = statusItem.button {
            button.title = "🧵 Синхр..."
        }
        executeShellAsync("./archive_poster.py --sync")
    }
    
    @objc func openArchiveLog() {
        NSWorkspace.shared.open(URL(fileURLWithPath: "\(projectDir)/archive_stdout.log"))
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
