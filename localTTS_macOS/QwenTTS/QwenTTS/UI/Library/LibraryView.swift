import SwiftUI
import AppKit

struct LibraryItem: Identifiable, Hashable {
    let id = UUID()
    let title: String
    let source: String
    let status: String
    let time: String
    let isPlaying: Bool
    let type: ItemType
    // 操作所需的后端标识（按类型择一使用）
    var savedIndex: Int? = nil   // saved/instant：在完整 saved_items 列表里的原始下标
    var md5: String? = nil       // saved/cache：用于删除/播放缓存
    var filename: String? = nil  // podcast：文件名
    var fullText: String = ""    // 完整文本（双击查看）
    var isPinned: Bool = false   // podcast：是否已置顶

    enum ItemType {
        case instant, saved, podcast, cache
    }
}

// MARK: - 视图模型：注入 coordinator，从真实后端拉取各分类内容
@MainActor
final class LibraryViewModel: ObservableObject {
    weak var coordinator: ApplicationCoordinator?

    @Published var items: [LibraryItem] = []
    @Published var isLoading = false

    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
    }

    private var apiClient: BackendAPIClient? {
        coordinator?.processManager.apiClient
    }

    // MARK: 时间格式化（timestamp 为秒级 Double）
    private func formatTime(_ timestamp: Double) -> String {
        let date = Date(timeIntervalSince1970: timestamp)
        let cal = Calendar.current
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        if cal.isDateInToday(date) {
            formatter.dateFormat = "今天 HH:mm"
        } else if cal.isDateInYesterday(date) {
            formatter.dateFormat = "昨天 HH:mm"
        } else {
            formatter.dateFormat = "M月d日"
        }
        return formatter.string(from: date)
    }

    private func truncate(_ text: String, _ n: Int = 40) -> String {
        if text.count <= n { return text }
        return String(text.prefix(n)) + "…"
    }

    private func filenameWithoutExtension(_ name: String) -> String {
        (name as NSString).deletingPathExtension
    }

    // MARK: 按分类加载
    func load(tab: Int) async {
        guard let client = apiClient else {
            items = []
            return
        }
        isLoading = true
        defer { isLoading = false }

        switch tab {
        case 0, 1:
            let raw = await client.fetchSavedItems() ?? []
            // 保留原始 index（用于 playSaved / deleteSaved）
            let mapped: [LibraryItem] = raw.enumerated().compactMap { (idx, dict) in
                let source = dict["source"] as? String ?? ""
                let isClipboard = (source == "clipboard")
                // tab0=即时阅读（剪贴板），tab1=稍后阅读（非剪贴板）
                if tab == 0 && !isClipboard { return nil }
                if tab == 1 && isClipboard { return nil }

                let text = dict["text"] as? String ?? ""
                let rawTitle = dict["title"] as? String ?? ""
                let title = rawTitle.isEmpty ? truncate(text) : rawTitle
                let timestamp = dict["timestamp"] as? Double ?? 0
                let md5 = dict["md5"] as? String
                let isPinned = dict["is_pinned"] as? Bool ?? false
                return LibraryItem(
                    title: title,
                    source: source.isEmpty ? "保存" : source,
                    status: isPinned ? "已置顶" : "已保存",
                    time: formatTime(timestamp),
                    isPlaying: false,
                    type: isClipboard ? .instant : .saved,
                    savedIndex: idx,
                    md5: md5,
                    fullText: text,
                    isPinned: isPinned
                )
            }
            items = mapped

        case 2:
            let raw = await client.fetchPodcasts() ?? []
            items = raw.map { dict in
                let filename = dict["filename"] as? String ?? ""
                let isPinned = dict["is_pinned"] as? Bool ?? false
                return LibraryItem(
                    title: filenameWithoutExtension(filename),
                    source: "播客",
                    status: isPinned ? "已置顶" : "就绪",
                    time: dict["date"] as? String ?? "",
                    isPlaying: false,
                    type: .podcast,
                    filename: filename,
                    isPinned: isPinned
                )
            }

        case 3:
            let raw = await client.fetchCacheItems() ?? []
            items = raw.map { dict in
                let text = dict["text"] as? String ?? ""
                let voice = dict["voice"] as? String ?? ""
                let model = dict["model"] as? String ?? ""
                let duration = dict["duration"]
                let durationStr: String
                if let d = duration as? Double {
                    durationStr = String(format: "%.1fs", d)
                } else if let d = duration as? Int {
                    durationStr = "\(d)s"
                } else {
                    durationStr = "\(duration ?? "")"
                }
                return LibraryItem(
                    title: truncate(text),
                    source: voice.isEmpty ? (model.isEmpty ? "缓存" : model) : voice,
                    status: durationStr,
                    time: dict["created_at"] as? String ?? "",
                    isPlaying: false,
                    type: .cache,
                    md5: dict["md5"] as? String,
                    fullText: text
                )
            }

        default:
            items = []
        }
        // ADR-003 F4: pinned-first for DISPLAY ONLY. savedIndex was already
        // captured from the backend's original order above, so play/delete still
        // hit the right item; this only changes what the user sees.
        items = items.filter { $0.isPinned } + items.filter { !$0.isPinned }
    }

    // MARK: 操作
    func fetchTranscript(filename: String) async -> String? {
        guard let client = coordinator?.processManager.apiClient else { return nil }
        return await client.fetchPodcastTranscript(filename: filename)
    }

    func play(_ item: LibraryItem) {
        guard let client = apiClient else { return }
        Task {
            switch item.type {
            case .instant, .saved:
                if let idx = item.savedIndex { _ = await client.playSaved(indices: [idx]) }
            case .podcast:
                if let filename = item.filename { _ = await client.playPodcast(filename: filename) }
            case .cache:
                if let md5 = item.md5 { _ = await client.playCache(md5: md5) }
            }
        }
    }

    func delete(_ item: LibraryItem, currentTab: Int) {
        guard let client = apiClient else { return }
        Task {
            switch item.type {
            case .instant, .saved:
                _ = await client.deleteSaved(md5: item.md5, index: item.savedIndex)
            case .podcast:
                if let filename = item.filename { _ = await client.deletePodcast(filename: filename) }
            case .cache:
                if let md5 = item.md5 { _ = await client.deleteCache(md5: md5) }
            }
            await load(tab: currentTab)
        }
    }

    /// 置顶/取消置顶播客（仅 .podcast 行有意义；后端 /podcasts/toggle_pin）。
    func togglePin(_ item: LibraryItem, currentTab: Int) {
        guard let client = apiClient else { return }
        Task {
            switch item.type {
            case .podcast:
                if let filename = item.filename { _ = await client.togglePodcastPin(filename: filename) }
            case .instant, .saved:
                if let md5 = item.md5 { _ = await client.toggleSavedPin(md5: md5) }
            default:
                return
            }
            await load(tab: currentTab)
        }
    }

    /// ADR-003 F3: turn a 即时/稍后阅读 item into a background single-voice podcast
    /// (generate_single_podcast is pure TTS — no LLM key needed, so no gate).
    func generatePodcast(_ item: LibraryItem) {
        guard let client = apiClient, !item.fullText.isEmpty else { return }
        Task {
            _ = await client.generateSinglePodcast(
                text: item.fullText, source: item.source, voice: nil, title: item.title
            )
        }
    }

    func clearCache() {
        guard let client = apiClient else { return }
        Task {
            _ = await client.clearCache()
            await load(tab: 3)
        }
    }
}

struct LibraryView: View {
    @ObservedObject var viewModel: LibraryViewModel

    @State private var selectedTab = 0
    @State private var hoveredItem: UUID? = nil
    @State private var selectedItems: Set<UUID> = []
    @State private var searchText = ""
    @State private var showingClearCacheConfirm = false
    @State private var textPreviewItem: LibraryItem? = nil   // 双击查看文本

    var filteredItems: [LibraryItem] {
        let items = viewModel.items
        if searchText.isEmpty { return items }
        return items.filter { $0.title.localizedCaseInsensitiveContains(searchText) }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header: Tabs & Tools
            VStack(spacing: 12) {
                HStack {
                    Picker("", selection: $selectedTab) {
                        Text("即时阅读").tag(0)
                        Text("稍后阅读").tag(1)
                        Text("播客文稿").tag(2)
                        Text("缓存").tag(3)
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 320)
                    .onChange(of: selectedTab) { _, newTab in
                        selectedItems.removeAll()
                        Task { await viewModel.load(tab: newTab) }
                    }
                    
                    Spacer()
                    
                    // Contextual Batch Action Bar or Search/Filter
                    if !selectedItems.isEmpty {
                        HStack(spacing: 12) {
                            Text("\(selectedItems.count) selected")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.secondary)
                            
                            Button(action: { selectedItems.removeAll() }) {
                                Text("Cancel")
                            }
                            .buttonStyle(.plain)
                            .foregroundColor(.blue)
                            
                            Button(action: {
                                let toDelete = filteredItems.filter { selectedItems.contains($0.id) }
                                for item in toDelete {
                                    viewModel.delete(item, currentTab: selectedTab)
                                }
                                selectedItems.removeAll()
                            }) {
                                Image(systemName: "trash")
                                    .foregroundColor(.red)
                            }
                            .buttonStyle(.plain)
                            .help("Delete Selected")
                        }
                    } else {
                        HStack(spacing: 8) {
                            // Search Field
                            HStack {
                                Image(systemName: "magnifyingglass")
                                    .foregroundColor(.secondary)
                                TextField("Search...", text: $searchText)
                                    .textFieldStyle(.plain)
                            }
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(Color(NSColor.controlBackgroundColor))
                            .cornerRadius(6)
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(NSColor.separatorColor), lineWidth: 1))
                            .frame(width: 160)
                            .help("搜索内容")
                            
                            // Filter/Sort
                            Menu {
                                Button("Sort by Date") {}
                                Button("Sort by Name") {}
                                Divider()
                                Button("Show Only Playing") {}
                            } label: {
                                Image(systemName: "line.3.horizontal.decrease.circle")
                            }
                            .menuStyle(.borderlessButton)
                            .frame(width: 24)
                            .help("筛选 / 排序")
                        }
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 16)
                
                // Cache Info Bar (Only visible in Cache tab)
                if selectedTab == 3 {
                    HStack {
                        Text("Storage Usage: \(viewModel.items.count) 项")
                            .font(.system(size: 12))
                            .foregroundColor(.secondary)
                        Text("•")
                            .foregroundColor(.secondary.opacity(0.5))
                        Text("\(filteredItems.count) Items")
                            .font(.system(size: 12))
                            .foregroundColor(.secondary)
                        
                        Spacer()
                        
                        Menu {
                            Button("Clear Selected", action: {
                                let toDelete = filteredItems.filter { selectedItems.contains($0.id) }
                                for item in toDelete {
                                    viewModel.delete(item, currentTab: selectedTab)
                                }
                                selectedItems.removeAll()
                            })
                                .disabled(selectedItems.isEmpty)
                            Divider()
                            Button("Clear All Cache", role: .destructive) {
                                showingClearCacheConfirm = true
                            }
                        } label: {
                            Text("Manage Cache...")
                                .font(.system(size: 12))
                        }
                        .menuStyle(.borderlessButton)
                        .confirmationDialog("Are you sure you want to clear all cache? This cannot be undone.", isPresented: $showingClearCacheConfirm) {
                            Button("Clear All", role: .destructive) {
                                viewModel.clearCache()
                            }
                            Button("Cancel", role: .cancel) {}
                        } message: {
                            Text("This will permanently delete all temporary audio files.")
                        }
                    }
                    .padding(.horizontal, 24)
                    .padding(.bottom, 8)
                } else {
                    Spacer().frame(height: 8)
                }
            }
            
            Divider()
            
            // Content Area
            if viewModel.isLoading {
                VStack {
                    Spacer()
                    ProgressView()
                        .scaleEffect(0.8)
                    Text("Loading items...")
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                        .padding(.top, 8)
                    Spacer()
                }
            } else if filteredItems.isEmpty {
                // Empty State
                VStack(spacing: 12) {
                    Spacer()
                    Image(systemName: "tray")
                        .font(.system(size: 48))
                        .foregroundColor(.secondary.opacity(0.5))
                    Text("No items found.")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundColor(.secondary)
                    Spacer()
                }
            } else {
                // List
                ScrollView {
                    LazyVStack(spacing: 1) {
                        ForEach(filteredItems) { item in
                            LibraryRowView(
                                item: item,
                                isHovered: hoveredItem == item.id,
                                isSelected: selectedItems.contains(item.id),
                                onPlay: { viewModel.play(item) },
                                onDelete: { viewModel.delete(item, currentTab: selectedTab) },
                                onPin: { viewModel.togglePin(item, currentTab: selectedTab) },
                                onGeneratePodcast: { viewModel.generatePodcast(item) }
                            )
                            .onHover { isHovered in
                                if isHovered {
                                    hoveredItem = item.id
                                } else if hoveredItem == item.id {
                                    hoveredItem = nil
                                }
                            }
                            .onTapGesture(count: 2) {
                                // 双击查看完整文本：saved/instant/cache 直接用 fullText；
                                // 播客异步取 .txt 文稿，取到则显示，否则播放。
                                if !item.fullText.isEmpty {
                                    textPreviewItem = item
                                } else if item.type == .podcast, let fn = item.filename {
                                    Task {
                                        let txt = await viewModel.fetchTranscript(filename: fn)
                                        if let txt = txt, !txt.isEmpty {
                                            var copy = item
                                            copy.fullText = txt
                                            textPreviewItem = copy
                                        } else {
                                            viewModel.play(item)
                                        }
                                    }
                                } else {
                                    viewModel.play(item)
                                }
                            }
                            .onTapGesture {
                                if selectedItems.contains(item.id) {
                                    selectedItems.remove(item.id)
                                } else {
                                    selectedItems.insert(item.id)
                                }
                            }
                        }
                    }
                    .padding(.vertical, 8)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clear)
        .task {
            await viewModel.load(tab: selectedTab)
        }
        .sheet(item: $textPreviewItem) { item in
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(item.title).font(.headline).lineLimit(2)
                    Spacer()
                    Button("播放") { viewModel.play(item) }
                    Button("关闭") { textPreviewItem = nil }
                }
                Divider()
                ScrollView {
                    Text(item.fullText)
                        .font(.system(size: 13))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(20)
            .frame(width: 560, height: 460)
        }
    }
}

struct LibraryRowView: View {
    let item: LibraryItem
    let isHovered: Bool
    let isSelected: Bool
    var onPlay: () -> Void = {}
    var onDelete: () -> Void = {}
    var onPin: () -> Void = {}
    var onGeneratePodcast: () -> Void = {}

    var body: some View {
        HStack(spacing: 16) {
            // Selection / Status Icon
            ZStack {
                if isSelected {
                    Circle()
                        .fill(Color.accentColor)
                        .frame(width: 24, height: 24)
                    Image(systemName: "checkmark")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundColor(.white)
                } else {
                    Circle()
                        .fill(item.isPlaying ? Color.blue.opacity(0.1) : Color.gray.opacity(0.1))
                        .frame(width: 32, height: 32)
                    
                    Image(systemName: item.isPlaying ? "speaker.wave.2.fill" : "doc.text.fill")
                        .foregroundColor(item.isPlaying ? .blue : .secondary)
                }
            }
            .frame(width: 32)
            
            // Text Content
            VStack(alignment: .leading, spacing: 4) {
                Text(item.title)
                    .font(.system(size: 14, weight: .medium))
                    .lineLimit(1)
                    .foregroundColor(isSelected ? .accentColor : .primary)
                
                HStack(spacing: 8) {
                    Text(item.source)
                        .font(.system(size: 11, weight: .semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.1))
                        .cornerRadius(4)
                        .foregroundColor(.secondary)
                    
                    Text("•")
                        .foregroundColor(.secondary.opacity(0.5))
                    
                    Text(item.status)
                        .font(.system(size: 12))
                        .foregroundColor(.secondary)
                }
            }
            
            Spacer()
            
            // Trailing Actions / Time
            if isHovered && !isSelected {
                HStack(spacing: 12) {
                    Button(action: { onPlay() }) { Image(systemName: "play.fill") }
                        .buttonStyle(.plain)
                        .help("播放")

                    // 生成播客：仅即时/稍后阅读行——用该条 fullText 起一个后台单人
                    // 播客任务（generate_single_podcast，纯 TTS，不需 LLM key）。
                    if item.type == .instant || item.type == .saved {
                        Button(action: { onGeneratePodcast() }) { Image(systemName: "waveform") }
                            .buttonStyle(.plain)
                            .help("生成播客")
                    }

                    // 置顶：播客 + 即时/稍后阅读均可（缓存行不可）。
                    if item.type != .cache {
                        Button(action: { onPin() }) {
                            Image(systemName: item.isPinned ? "pin.fill" : "pin")
                        }
                        .buttonStyle(.plain)
                        .foregroundColor(item.isPinned ? .accentColor : .secondary)
                        .help(item.isPinned ? "取消置顶" : "置顶")
                    }

                    Button(action: { onDelete() }) { Image(systemName: "trash") }
                        .buttonStyle(.plain)
                        .foregroundColor(.red)
                        .help("删除")
                }
                .padding(.trailing, 8)
                .foregroundColor(.secondary)
            } else {
                Text(item.time)
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(isSelected ? Color.accentColor.opacity(0.1) : (isHovered ? Color.secondary.opacity(0.05) : Color.clear))
        .contentShape(Rectangle())
    }
}

class LibraryHostingController: NSHostingController<LibraryView> {
    weak var coordinator: ApplicationCoordinator?

    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        let viewModel = LibraryViewModel(coordinator: coordinator)
        super.init(rootView: LibraryView(viewModel: viewModel))
    }
    
    @MainActor required dynamic init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
