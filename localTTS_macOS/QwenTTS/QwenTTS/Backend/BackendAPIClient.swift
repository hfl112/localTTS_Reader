import Foundation

/// 主 actor 隔离：所有调用方（@MainActor 的 BackendProcessManager 与各 ViewController）
/// 本就在主线程，借此让 `managementToken` 等可变状态的读写在主 actor 串行化，消除并发
/// Task（健康轮询 / 快照轮询 / 用户动作）对其的无同步写竞争。网络 `await session.data`
/// 在 URLSession 自有线程执行、挂起期间释放主 actor，不阻塞 UI。
@MainActor
class BackendAPIClient {
    let port: Int
    var managementToken: String = ""
    private let session: URLSession

    /// 非 nil 时(仅 `--mock-backend`)所有请求由内存 mock 应答,不发起网络请求。
    var mock: MockBackend?

    // MARK: - 错误冒泡（不再静默吞掉传输错误）
    /// 最近一次传输错误描述（含 path 与底层 error）。成功请求不会清空它，
    /// 由上层（BackendProcessManager / AppStateStore）根据轮询结果决定连接健康度。
    private(set) var lastTransportError: String?
    /// 发生传输错误时的回调（含描述信息），供上层更新连接状态/提示 UI。
    var onTransportError: ((String) -> Void)?

    /// 记录一次传输错误：保存到 lastTransportError 并触发回调。
    private func recordTransportError(path: String, error: Error) {
        let message = "\(path): \(error.localizedDescription)"
        lastTransportError = message
        print("[APIClient] transport error \(message)")
        onTransportError?(message)
    }

    init(port: Int) {
        self.port = port
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 5.0
        self.session = URLSession(configuration: config)
    }

    private var baseURL: URL {
        return URL(string: "http://127.0.0.1:\(port)")!
    }

    // 注：requireToken 参数在 managementToken 已被 checkHealth 种下后其实是冗余的——
    // 一旦 managementToken 非空，所有请求都会自动带上该头。保留该参数仅为表达调用方意图，
    // 不影响认证正确性。此处刻意不重构，避免破坏现在能跑的认证流程。
    private func postJSON(path: String, body: [String: Any]?, requireToken: Bool = false) async -> (statusCode: Int, data: Data?) {
        if let mock = mock {
            return mock.respond(method: "POST", path: path, body: body)
        }
        guard let url = URL(string: path, relativeTo: baseURL) else { return (0, nil) }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if requireToken || !managementToken.isEmpty {
            request.setValue(managementToken, forHTTPHeaderField: "x-management-token")
        }
        
        if let body = body {
            do {
                request.httpBody = try JSONSerialization.data(withJSONObject: body)
            } catch {
                return (0, nil)
            }
        }
        
        do {
            let (data, response) = try await session.data(for: request)
            if let httpResponse = response as? HTTPURLResponse {
                return (httpResponse.statusCode, data)
            }
        } catch {
            recordTransportError(path: "POST \(path)", error: error)
        }
        return (0, nil)
    }

    private func getJSON(path: String, requireToken: Bool = false) async -> (statusCode: Int, data: Data?) {
        if let mock = mock {
            return mock.respond(method: "GET", path: path, body: nil)
        }
        guard let url = URL(string: path, relativeTo: baseURL) else { return (0, nil) }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        if requireToken || !managementToken.isEmpty {
            request.setValue(managementToken, forHTTPHeaderField: "x-management-token")
        }
        
        do {
            let (data, response) = try await session.data(for: request)
            if let httpResponse = response as? HTTPURLResponse {
                return (httpResponse.statusCode, data)
            }
        } catch {
            recordTransportError(path: "GET \(path)", error: error)
        }
        return (0, nil)
    }

    // MARK: - Core Control APIs

    func checkHealth(token: String) async -> (alive: Bool, instanceId: String?) {
        self.managementToken = token
        let (status, data) = await getJSON(path: "/health", requireToken: true)
        guard status == 200, let data = data else { return (false, nil) }
        do {
            if let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               let appStatus = json["status"] as? String, appStatus == "ready",
               let instanceId = json["instance_id"] as? String {
                return (true, instanceId)
            }
        } catch {}
        return (false, nil)
    }

    func requestShutdown(token: String) async -> Bool {
        self.managementToken = token
        let (status, _) = await postJSON(path: "/control/shutdown", body: nil, requireToken: true)
        return status == 200
    }

    func readText(text: String, voice: String?, performanceProfile: String?, mode: String? = nil) async -> Bool {
        var body: [String: Any] = [
            "text": text,
            "source": "clipboard",
            "from_saved": false
        ]
        if let voice = voice { body["voice"] = voice }
        if let performanceProfile = performanceProfile { body["performance_profile"] = performanceProfile }
        if let mode = mode { body["mode"] = mode }

        let (status, _) = await postJSON(path: "/read", body: body)
        return status == 200
    }

    /// 首启向导「一键试音」：朗读固定短句并**等待真实结果**。
    /// 返回 nil 表示真的出声（成功）；返回非 nil 字符串为失败原因（可直接展示）。
    /// 不同于 readText 只看 HTTP 200——后端会阻塞到产生音频帧或捕获到推理错误才回复，
    /// 因此能区分"听到声音"与"模型缺失/加载失败导致无声"。
    func selfTestVoice() async -> String? {
        let (status, data) = await postJSON(path: "/selftest/voice", body: nil, requireToken: true)
        guard status == 200, let data = data,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return "试音请求失败（后端无响应或返回异常，HTTP \(status)）"
        }
        if (json["ok"] as? Bool) == true { return nil }
        return (json["error"] as? String) ?? "试音失败：未产生音频"
    }

    func stopPlayback() async -> Bool {
        let (status, _) = await postJSON(path: "/stop", body: nil, requireToken: true)
        return status == 200
    }

    func pausePlayback() async -> Bool {
        let (status, _) = await postJSON(path: "/pause", body: nil)
        return status == 200
    }

    func resumePlayback() async -> Bool {
        let (status, _) = await postJSON(path: "/resume", body: nil)
        return status == 200
    }

    func seekPlayback(direction: Int) async -> Bool {
        let (status, _) = await postJSON(path: "/seek", body: ["direction": direction])
        return status == 200
    }

    func fetchSnapshot() async -> Snapshot? {
        let (status, data) = await getJSON(path: "/snapshot")
        guard status == 200, let data = data else { return nil }
        return try? JSONDecoder().decode(Snapshot.self, from: data)
    }

    func updateSettings(settings: [String: Any], token: String) async -> Bool {
        self.managementToken = token
        if let mock = mock {
            return mock.respond(method: "PATCH", path: "/settings", body: settings).0 == 200
        }
        guard let url = URL(string: "/settings", relativeTo: baseURL) else { return false }
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(token, forHTTPHeaderField: "x-management-token")
        do {
            request.httpBody = try JSONSerialization.data(withJSONObject: settings)
            let (_, response) = try await session.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            recordTransportError(path: "PATCH /settings", error: error)
            return false
        }
    }

    func fetchSettings() async -> SettingsModel? {
        let (status, data) = await getJSON(path: "/settings")
        guard status == 200, let data = data else { return nil }
        return try? JSONDecoder().decode(SettingsModel.self, from: data)
    }

    // MARK: - AI 引擎 / 翻译配置

    func fetchEngines() async -> EngineConfig? {
        let (status, data) = await getJSON(path: "/engines", requireToken: true)
        guard status == 200, let data = data else { return nil }
        return try? JSONDecoder().decode(EngineConfig.self, from: data)
    }

    func updateEngines(_ body: [String: Any], token: String) async -> Bool {
        self.managementToken = token
        if let mock = mock {
            return mock.respond(method: "PATCH", path: "/engines", body: body).0 == 200
        }
        guard let url = URL(string: "/engines", relativeTo: baseURL) else { return false }
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(token, forHTTPHeaderField: "x-management-token")
        do {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            let (_, response) = try await session.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 200
        } catch {
            recordTransportError(path: "PATCH /engines", error: error)
            return false
        }
    }

    /// 检测某供应商连通性。POST /engines/check（带管理令牌）。
    /// 请求体：{ family, provider, key, region }；响应：{ ok, message }。
    /// 解析失败或传输错误时返回 (false, 友好错误文案)。
    func checkEngine(family: String, provider: String, key: String?, region: String?, token: String) async -> (ok: Bool, message: String) {
        self.managementToken = token
        var body: [String: Any] = [
            "family": family,
            "provider": provider,
            "key": key ?? ""
        ]
        if let region = region { body["region"] = region }

        let (status, data) = await postJSON(path: "/engines/check", body: body, requireToken: true)
        guard let data = data else {
            return (false, "无法连接后端（HTTP \(status)）")
        }
        if let result = try? JSONDecoder().decode(EngineCheckResult.self, from: data) {
            return (result.ok, result.message ?? (result.ok ? "验证成功" : "验证失败"))
        }
        return (false, "返回解析失败（HTTP \(status)）")
    }

    // MARK: - Saved Items Queue

    func saveForLater(text: String, source: String = "web", voice: String? = nil, title: String? = nil) async -> Bool {
        var body: [String: Any] = ["text": text, "source": source]
        if let voice = voice { body["voice"] = voice }
        if let title = title { body["title"] = title }
        
        let (status, _) = await postJSON(path: "/save_for_later", body: body)
        return status == 200
    }

    func fetchSavedItems() async -> [[String: Any]]? {
        let (status, data) = await getJSON(path: "/saved_items")
        guard status == 200, let data = data else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
    }

    func playSaved(indices: [Int]) async -> Bool {
        let (status, _) = await postJSON(path: "/play_saved", body: ["indices": indices])
        return status == 200
    }

    func deleteSaved(md5: String?, index: Int?) async -> Bool {
        var body: [String: Any] = [:]
        if let md5 = md5 { body["md5"] = md5 }
        if let index = index { body["index"] = index }
        let (status, _) = await postJSON(path: "/delete_saved", body: body)
        return status == 200
    }

    func clearSavedItems() async -> Bool {
        let (status, _) = await postJSON(path: "/saved_items/clear", body: nil)
        return status == 200
    }

    // MARK: - URL Reader

    func readUrl(url: String, html: String = "", translate: Bool = false, mode: String = "original", save: Bool = false, podcast: Bool = false) async -> Bool {
        let body: [String: Any] = [
            "url": url,
            "html": html,
            "translate": translate,
            "mode": mode,
            "save": save,
            "podcast": podcast
        ]
        let (status, _) = await postJSON(path: "/read_url", body: body)
        return status == 200
    }

    func fetchUrlJobs() async -> [[String: Any]]? {
        let (status, data) = await getJSON(path: "/url_jobs")
        guard status == 200, let data = data else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
    }

    // MARK: - Podcast Management

    func generateSinglePodcast(text: String, source: String = "web", voice: String? = nil, title: String? = nil, performanceProfile: String = "quiet") async -> Bool {
        var body: [String: Any] = ["text": text, "source": source, "performance_profile": performanceProfile]
        if let voice = voice { body["voice"] = voice }
        if let title = title { body["title"] = title }
        
        let (status, _) = await postJSON(path: "/generate_single_podcast", body: body)
        return status == 200
    }

    func generatePodcast() async -> Bool {
        let (status, _) = await postJSON(path: "/generate_podcast", body: nil)
        return status == 200
    }

    func fetchPodcasts() async -> [[String: Any]]? {
        let (status, data) = await getJSON(path: "/podcasts/list")
        guard status == 200, let data = data else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
    }

    func fetchPodcastTranscript(filename: String) async -> String? {
        let encoded = filename.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? filename
        let (status, data) = await getJSON(path: "/podcasts/transcript?filename=\(encoded)")
        guard status == 200, let data = data,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return json["text"] as? String
    }

    func fetchPodcastJobs() async -> [[String: Any]]? {
        let (status, data) = await getJSON(path: "/podcasts/jobs")
        guard status == 200, let data = data else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
    }

    func togglePodcastPin(filename: String) async -> Bool {
        let (status, _) = await postJSON(path: "/podcasts/toggle_pin", body: ["filename": filename])
        return status == 200
    }

    func deletePodcast(filename: String) async -> Bool {
        let (status, _) = await postJSON(path: "/podcasts/delete", body: ["filename": filename])
        return status == 200
    }

    func playPodcast(filename: String) async -> Bool {
        let (status, _) = await postJSON(path: "/podcasts/play", body: ["filename": filename])
        return status == 200
    }

    func clearPodcasts() async -> Bool {
        let (status, _) = await postJSON(path: "/podcasts/clear", body: nil)
        return status == 200
    }

    // MARK: - Cache Management

    func fetchCacheItems() async -> [[String: Any]]? {
        let (status, data) = await getJSON(path: "/cache/items")
        guard status == 200, let data = data else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
    }

    func playCache(md5: String) async -> Bool {
        let (status, _) = await postJSON(path: "/cache/play", body: ["md5": md5])
        return status == 200
    }

    func exportCache(md5: String) async -> Bool {
        let (status, _) = await postJSON(path: "/cache/export", body: ["md5": md5])
        return status == 200
    }

    func deleteCache(md5: String) async -> Bool {
        let (status, _) = await postJSON(path: "/cache/delete", body: ["md5": md5])
        return status == 200
    }

    func clearCache() async -> Bool {
        let (status, _) = await postJSON(path: "/cache/clear", body: nil)
        return status == 200
    }
}
