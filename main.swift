import Cocoa
import SQLite3

extension Int {
    func formattedWithSeparator() -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.groupingSeparator = " "
        return formatter.string(from: NSNumber(value: self)) ?? "\(self)"
    }
}

class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    var timer: Timer?
    var gitTimer: Timer?
    var isSyncingGit = false
    let dbPath = "/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads/crosspost.db"
    let projectDir = "/Users/mihaildremin/.gemini/antigravity/scratch/tg_to_threads"
    
    var viewMode: String {
        get { UserDefaults.standard.string(forKey: "menuBarViewMode") ?? "minimal" }
        set { 
            UserDefaults.standard.set(newValue, forKey: "menuBarViewMode")
            updateUI()
            rebuildMenu()
        }
    }
    
    struct Stats {
        var published: Int = 0
        var pending: Int = 0
        var failed: Int = 0
        var total: Int { published + pending + failed }
        var nextId: Int? = nil
        var nextDate: String = ""
        var nextPhotoCount: Int = 0
        var isPaused: Bool = false
        var nextPubShort: String = ""
        var nextPubLabel: String = ""
        var totalViews: Int = 0
        var totalLikes: Int = 0
        var threadsFollowers: Int = 1236
        var threadsGrowth: Int = 1
        var twitterFollowers: Int = 526
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

    func formatCompact(_ num: Int) -> String {
        if num >= 1_000_000 {
            return String(format: "%.1fM", Double(num) / 1_000_000.0)
        } else if num >= 1_000 {
            return String(format: "%.1fK", Double(num) / 1_000.0)
        }
        return "\(num)"
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
              (SELECT photos_json FROM archive_posts WHERE status = 'pending' ORDER BY post_id ASC LIMIT 1),
              (SELECT value FROM app_settings WHERE key = 'posting_paused' LIMIT 1)
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
                    if let p = sqlite3_column_text(stmt, 6) {
                        stats.isPaused = String(cString: p) == "1"
                    }
                }
                sqlite3_finalize(stmt)
            }
            sqlite3_close(db)
        }

        // Read status.json for analytics & schedule
        if let data = try? Data(contentsOf: URL(fileURLWithPath: "\(projectDir)/status.json")),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let paused = json["is_paused"] as? Bool {
                stats.isPaused = paused
            }
            if let nextPub = json["next_publication"] as? [String: Any] {
                stats.nextPubShort = nextPub["short_label"] as? String ?? ""
                stats.nextPubLabel = nextPub["full_label"] as? String ?? ""
            }
            if let a = json["analytics"] as? [String: Any] {
                stats.totalViews = a["total_views"] as? Int ?? 0
                stats.totalLikes = a["total_reactions"] as? Int ?? ((a["totals"] as? [String: Any])?["likes"] as? Int ?? 0)
                if let th = a["threads"] as? [String: Any] {
                    stats.threadsFollowers = th["followers"] as? Int ?? 1236
                    stats.threadsGrowth = th["growth"] as? Int ?? 1
                }
                if let tw = a["twitter"] as? [String: Any] {
                    stats.twitterFollowers = tw["followers"] as? Int ?? 526
                }
            }
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
        
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let button = self.statusItem.button else { return }
            if self.isSyncingGit {
                button.title = "🧵 🔄 ..."
                return
            }
            let viewsStr = self.formatCompact(stats.totalViews)
            let mode = self.viewMode
            
            if mode == "icon" {
                button.title = stats.isPaused ? "🧵⏸" : "🧵"
            } else if mode == "detailed" {
                button.title = stats.isPaused ? "🧵 ⏸ \(stats.published)/\(stats.total)" : "🧵 \(stats.published)/\(stats.total) • 👁️ \(viewsStr)"
            } else {
                // "minimal" default: fits easily on any display without notch clipping
                button.title = stats.isPaused ? "🧵 ⏸ \(stats.published)/\(stats.total)" : "🧵 \(stats.published)/\(stats.total)"
            }
            
            button.toolTip = """
            🧵 Threads & 𝕏 Poster (\(stats.isPaused ? "На паузе ⏸" : "Активен 🟢"))
            • Прогресс: \(stats.published) / \(stats.total) (\(String(format: "%.1f", stats.percent))%)
            • Охват: \(stats.totalViews.formattedWithSeparator()) просмотров
            • Лайки: \(stats.totalLikes.formattedWithSeparator())
            • Следующий пост: \(stats.nextPubLabel.isEmpty ? "—" : stats.nextPubLabel)
            (Кликните для меню управления)
            """
        }
    }
    
    func rebuildMenu() {
        guard let menu = statusItem.menu else { return }
        menu.removeAllItems()
        
        let stats = fetchStats()
        
        // Header
        let headerItem = NSMenuItem(title: "🧵 Threads & 𝕏 Auto-Poster", action: nil, keyEquivalent: "")
        let titleAttr: [NSAttributedString.Key: Any] = [
            .font: NSFont.boldSystemFont(ofSize: 14)
        ]
        headerItem.attributedTitle = NSAttributedString(string: "🧵 Threads & 𝕏 Auto-Poster", attributes: titleAttr)
        headerItem.isEnabled = false
        menu.addItem(headerItem)

        let statusText = stats.isPaused ? "⏸️ Статус постинга: НА ПАУЗЕ" : "🟢 Статус постинга: АКТИВЕН (24/7)"
        let statusItemRow = NSMenuItem(title: statusText, action: nil, keyEquivalent: "")
        statusItemRow.isEnabled = false
        menu.addItem(statusItemRow)
        
        menu.addItem(NSMenuItem.separator())

        // Analytics & Reactions Section
        let anHeader = NSMenuItem(title: "📊 Охваты и реакции по всем опубликованным постам:", action: nil, keyEquivalent: "")
        anHeader.isEnabled = false
        menu.addItem(anHeader)

        let viewsRow = NSMenuItem(title: "   • 👁️ Просмотры всех постов: \(stats.totalViews.formattedWithSeparator())", action: nil, keyEquivalent: "")
        viewsRow.isEnabled = false
        menu.addItem(viewsRow)

        let likesRow = NSMenuItem(title: "   • ❤️ Лайки всех постов: \(stats.totalLikes.formattedWithSeparator())", action: nil, keyEquivalent: "")
        likesRow.isEnabled = false
        menu.addItem(likesRow)

        let growthStr = stats.threadsGrowth > 0 ? "+\(stats.threadsGrowth)" : "\(stats.threadsGrowth)"
        let audRow = NSMenuItem(title: "   • 👥 Подписчики: 🧵 \(stats.threadsFollowers) (\(growthStr))  |  𝕏 \(stats.twitterFollowers)", action: nil, keyEquivalent: "")
        audRow.isEnabled = false
        menu.addItem(audRow)

        menu.addItem(NSMenuItem.separator())
        
        // Queue Progress Section
        let progressStr = String(format: "📦 Опубликовано: %d из %d (%.1f%%)", stats.published, stats.total, stats.percent)
        let progItem = NSMenuItem(title: progressStr, action: nil, keyEquivalent: "")
        progItem.isEnabled = false
        menu.addItem(progItem)
        
        let daysLeft = Double(stats.pending) / 24.0
        let leftStr = String(format: "⏳ В очереди архива: %d постов (~%.1f дней)", stats.pending, daysLeft)
        let leftItem = NSMenuItem(title: leftStr, action: nil, keyEquivalent: "")
        leftItem.isEnabled = false
        menu.addItem(leftItem)

        let nextPubText = stats.isPaused ? "⏸️ След. публикация: На паузе" : "⏰ След. публикация: \(stats.nextPubLabel.isEmpty ? "Раз в час" : stats.nextPubLabel)"
        let nextPubRow = NSMenuItem(title: nextPubText, action: nil, keyEquivalent: "")
        nextPubRow.isEnabled = false
        menu.addItem(nextPubRow)
        
        if let nextId = stats.nextId {
            let cleanDate = stats.nextDate.prefix(10)
            let nextStr = "📸 В очереди следующий: #\(nextId) (\(cleanDate), \(stats.nextPhotoCount) фото)"
            let nextItem = NSMenuItem(title: nextStr, action: nil, keyEquivalent: "")
            nextItem.isEnabled = false
            menu.addItem(nextItem)
        }
        
        menu.addItem(NSMenuItem.separator())
        
        // Pause / Resume Action
        let pauseTitle = stats.isPaused ? "▶️ Возобновить автоматический постинг" : "⏸️ Поставить постинг на паузу"
        let pauseItem = NSMenuItem(title: pauseTitle, action: #selector(togglePause), keyEquivalent: "p")
        pauseItem.target = self
        menu.addItem(pauseItem)

        let gitSyncItem = NSMenuItem(title: "🔄 Синхронизировать с GitHub прямо сейчас", action: #selector(manualGitSync), keyEquivalent: "g")
        gitSyncItem.target = self
        menu.addItem(gitSyncItem)

        let triggerArchiveItem = NSMenuItem(title: "⚡ Опубликовать 1 архивный пост в облаке", action: #selector(openArchiveActions), keyEquivalent: "")
        triggerArchiveItem.target = self
        menu.addItem(triggerArchiveItem)

        let triggerCrosspostItem = NSMenuItem(title: "⚡ Проверить новые посты Telegram в облаке", action: #selector(openCrosspostActions), keyEquivalent: "")
        triggerCrosspostItem.target = self
        menu.addItem(triggerCrosspostItem)
        
        menu.addItem(NSMenuItem.separator())
        
        // Quick Links
        let pwaLink = NSMenuItem(title: "📱 Открыть PWA на телефоне / в браузере", action: #selector(openPWAPage), keyEquivalent: "")
        pwaLink.target = self
        menu.addItem(pwaLink)

        let ghActionsLink = NSMenuItem(title: "🌐 Открыть страницу GitHub Actions", action: #selector(openGitHubActions), keyEquivalent: "")
        ghActionsLink.target = self
        menu.addItem(ghActionsLink)

        let threadsLink = NSMenuItem(title: "🌐 Открыть профиль Threads (@dremin)", action: #selector(openThreadsProfile), keyEquivalent: "")
        threadsLink.target = self
        menu.addItem(threadsLink)

        let twitterLink = NSMenuItem(title: "🌐 Открыть профиль Twitter/X (@m_dremin)", action: #selector(openTwitterProfile), keyEquivalent: "")
        twitterLink.target = self
        menu.addItem(twitterLink)
        
        let folderItem = NSMenuItem(title: "📂 Открыть папку проекта в Finder", action: #selector(openFolder), keyEquivalent: "")
        folderItem.target = self
        menu.addItem(folderItem)
        
        // View format options
        let viewMenu = NSMenu()
        let mode = self.viewMode
        
        let minItem = NSMenuItem(title: "Лаконичный: 🧵 \(stats.published)/\(stats.total)", action: #selector(setModeMinimal), keyEquivalent: "")
        minItem.target = self
        minItem.state = (mode == "minimal") ? .on : .off
        viewMenu.addItem(minItem)

        let detItem = NSMenuItem(title: "С охватом: 🧵 \(stats.published)/\(stats.total) • 👁️ \(self.formatCompact(stats.totalViews))", action: #selector(setModeDetailed), keyEquivalent: "")
        detItem.target = self
        detItem.state = (mode == "detailed") ? .on : .off
        viewMenu.addItem(detItem)
        
        let iconItem = NSMenuItem(title: "Только иконка: 🧵", action: #selector(setModeIcon), keyEquivalent: "")
        iconItem.target = self
        iconItem.state = (mode == "icon") ? .on : .off
        viewMenu.addItem(iconItem)
        
        let viewMenuItem = NSMenuItem(title: "📐 Формат значка в строке меню...", action: nil, keyEquivalent: "")
        viewMenuItem.submenu = viewMenu
        menu.addItem(viewMenuItem)

        menu.addItem(NSMenuItem.separator())
        
        // Quit
        let quitItem = NSMenuItem(title: "✕ Закрыть статусбар", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    @objc func setModeMinimal() { viewMode = "minimal" }
    @objc func setModeDetailed() { viewMode = "detailed" }
    @objc func setModeIcon() { viewMode = "icon" }

    @objc func togglePause() {
        let stats = fetchStats()
        let currentlyPaused = stats.isPaused
        let action = currentlyPaused ? "--resume" : "--pause"
        
        // 1. Run local script
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["\(projectDir)/archive_poster.py", action]
        p.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        try? p.run()
        p.waitUntilExit()

        let p2 = Process()
        p2.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p2.arguments = ["\(projectDir)/update_status.py"]
        p2.currentDirectoryURL = URL(fileURLWithPath: projectDir)
        try? p2.run()
        p2.waitUntilExit()

        self.updateUI()
        self.rebuildMenu()

        // 2. Push to GitHub in background so cloud actions sync
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let gitP = Process()
            gitP.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            gitP.arguments = ["add", "crosspost.db", "status.json", "docs/status.json"]
            gitP.currentDirectoryURL = URL(fileURLWithPath: self.projectDir)
            try? gitP.run()
            gitP.waitUntilExit()

            let gitC = Process()
            gitC.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            gitC.arguments = ["commit", "-m", currentlyPaused ? "Resume posting from macOS statusbar [skip ci]" : "Pause posting from macOS statusbar [skip ci]"]
            gitC.currentDirectoryURL = URL(fileURLWithPath: self.projectDir)
            try? gitC.run()
            gitC.waitUntilExit()

            let gitPush = Process()
            gitPush.executableURL = URL(fileURLWithPath: "/usr/bin/git")
            gitPush.arguments = ["push", "origin", "main"]
            gitPush.currentDirectoryURL = URL(fileURLWithPath: self.projectDir)
            try? gitPush.run()
            gitPush.waitUntilExit()

            DispatchQueue.main.async {
                self.updateUI()
                self.rebuildMenu()
            }
        }
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
    
    @objc func openArchiveActions() {
        if let url = URL(string: "https://github.com/DreminMS/tg-to-threads-x/actions/workflows/archive_poster.yml") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openCrosspostActions() {
        if let url = URL(string: "https://github.com/DreminMS/tg-to-threads-x/actions/workflows/crosspost.yml") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openPWAPage() {
        if let url = URL(string: "https://dreminms.github.io/tg-to-threads-x/") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openGitHubActions() {
        if let url = URL(string: "https://github.com/DreminMS/tg-to-threads-x/actions") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openThreadsProfile() {
        if let url = URL(string: "https://www.threads.net/@dremin") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openTwitterProfile() {
        if let url = URL(string: "https://x.com/m_dremin") {
            NSWorkspace.shared.open(url)
        }
    }
    
    @objc func openFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: projectDir))
    }
    
    @objc func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}

// Entry Point
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
