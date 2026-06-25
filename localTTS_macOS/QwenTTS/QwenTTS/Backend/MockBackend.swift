import Foundation

/// 内存 Mock 后端：在 `--mock-backend` 下替代真实 Python 后端。
///
/// 按 method+path 返回固定 fixture，并让状态变更类 POST（/read、/pause、/resume、/stop）
/// 改变内部播放状态，使 `/snapshot` 反映一个简单状态机。仅用于 UI 验证，不依赖
/// 模型 / MLX / ffmpeg / 真实音频设备（对应 FRONTEND_VALIDATION_AND_UX_PLAN.md §4）。
///
/// 通过启动参数可切换初始 fixture 与失败注入：
///   --mock-state=speaking|paused|idle|cooling
///   --mock-failure=connection|llm_no_key|youtube_no_transcript|engine_timeout|engine_auth_error
final class MockBackend {

    enum PlaybackState: String {
        case idle, speaking, paused, cooling
    }

    /// 失败注入（对应 §4.4），用于验证前台失败提示是否可执行。
    enum Failure: String {
        case none
        case connection            // /snapshot 超时/500：触发连接错误 UI
        case llmNoKey              // 未配置 LLM key：应引导去「AI 引擎」页
        case youtubeNoTranscript   // YouTube 无字幕/地区限制
        case engineTimeout         // 网络超时
        case engineAuthError       // 鉴权失败
    }

    private(set) var state: PlaybackState
    var failure: Failure
    private let instanceId = "mock-backend"

    // 任务是否已被 dispatch:模拟真实后端「job 在请求后才出现」的行为,
    // 使前台的 baseline(提交前)为空、提交后才看到新 job(成功或失败)。
    private var urlJobActive = false
    private var podcastJobActive = false

    init(state: PlaybackState = .speaking, failure: Failure = .none) {
        self.state = state
        self.failure = failure
    }

    /// 从进程启动参数构造，支持 --mock-state / --mock-failure 切换 fixture。
    static func fromProcessArgs() -> MockBackend {
        let args = ProcessInfo.processInfo.arguments
        func value(for flag: String) -> String? {
            for a in args where a.hasPrefix(flag + "=") {
                return String(a.dropFirst(flag.count + 1))
            }
            return nil
        }
        let state = PlaybackState(rawValue: value(for: "--mock-state") ?? "") ?? .speaking
        let failure = Failure(rawValue: (value(for: "--mock-failure") ?? "").camelizedFailureKey) ?? .none
        return MockBackend(state: state, failure: failure)
    }

    // MARK: - 请求分派

    /// 返回 (statusCode, body)。BackendAPIClient 在 mock 非 nil 时直接调用它，不碰网络。
    func respond(method: String, path: String, body: [String: Any]?) -> (Int, Data?) {
        let route = String(path.split(separator: "?").first ?? Substring(path))

        // 连接失败 fixture：/snapshot 返回 500，触发 AppStateStore.reportConnectionError。
        if failure == .connection, route == "/snapshot" {
            return (500, nil)
        }

        switch (method, route) {
        // --- Core Playback (§4.1) ---
        case ("GET", "/health"):
            return json(["status": "ready", "instance_id": instanceId, "pid": 0])
        case ("GET", "/snapshot"):
            return json(snapshotDict())
        case ("POST", "/read"):
            state = .speaking
            return ok()
        case ("POST", "/pause"):
            state = .paused
            return ok()
        case ("POST", "/resume"):
            state = .speaking
            return ok()
        case ("POST", "/stop"):
            state = .idle
            return ok()
        case ("POST", "/seek"):
            return ok()
        case ("POST", "/control/shutdown"):
            return ok()

        // --- Settings / Engines (§4.2) ---
        case ("GET", "/settings"):
            return json(["model": "Qwen3-TTS", "voice": "Serena", "performance_profile": "balanced"])
        case ("PATCH", "/settings"):
            return ok()
        case ("GET", "/engines"):
            return json(["llm": ["selected": "gemini", "models": [:]], "translate": ["provider": "google", "target_lang": "zh"]])
        case ("PATCH", "/engines"):
            return ok()
        case ("POST", "/engines/check"):
            return engineCheckResult()

        // --- Saved Items (§4.2) ---
        case ("GET", "/saved_items"):
            return json([["title": "Mock 保存项", "md5": "mockmd5", "source": "web"]])
        case ("POST", "/save_for_later"), ("POST", "/play_saved"),
             ("POST", "/delete_saved"), ("POST", "/saved_items/clear"):
            return ok()

        // --- URL / Podcast / Cache (§4.3) ---
        case ("POST", "/read_url"):
            // URL 任务可能同时产生 url 处理与播客渲染两类 job。
            urlJobActive = true
            podcastJobActive = true
            return ok()
        case ("GET", "/url_jobs"):
            return json(urlJobActive ? urlJobsFixture() : [])
        case ("POST", "/generate_single_podcast"), ("POST", "/generate_podcast"):
            podcastJobActive = true
            return ok()
        case ("GET", "/podcasts/list"):
            return json([["filename": "mock_podcast.wav", "title": "Mock Podcast", "pinned": false]])
        case ("GET", "/podcasts/jobs"):
            return json(podcastJobActive ? podcastJobsFixture() : [])
        case ("GET", "/podcasts/transcript"):
            return json(["text": "Mock transcript line 1\nMock transcript line 2"])
        case ("POST", "/podcasts/play"), ("POST", "/podcasts/delete"),
             ("POST", "/podcasts/toggle_pin"), ("POST", "/podcasts/clear"):
            return ok()
        case ("GET", "/cache/items"):
            return json([["md5": "cachemd5", "title": "Mock Cache", "size": 12345]])
        case ("POST", "/cache/play"), ("POST", "/cache/export"),
             ("POST", "/cache/delete"), ("POST", "/cache/clear"):
            return ok()
        case ("GET", "/debug/state"), ("GET", "/debug/events"):
            return json([:])

        default:
            // 未建模端点：返回空成功，避免 UI 误报为连接失败。
            return json([:])
        }
    }

    // MARK: - Fixture 构造

    private func snapshotDict() -> [String: Any] {
        let playing = (state == .speaking || state == .paused)
        let statusCode: String
        switch state {
        case .idle: statusCode = "IDLE"
        case .speaking, .paused: statusCode = "BUSY"
        case .cooling: statusCode = "COOLING"
        }
        return [
            "main_title": "Mock Article",
            "main_progress": "2/8",
            "main_is_playing": playing,
            "is_paused": state == .paused,
            "status_code": statusCode,
            "current_article_chunks": ["第一段", "第二段", "第三段"],
            "current_article_index": 1,
            "active_podcast_processes": 0,
            "active_url_tasks": [],
            "instance_id": instanceId
        ]
    }

    /// /engines/check 结果随失败注入而变，支撑流程 B/D 的失败提示验证。
    private func engineCheckResult() -> (Int, Data?) {
        switch failure {
        case .llmNoKey:
            return json(["ok": false, "message": "未配置 API key，请在「AI 引擎」页填写后重试"])
        case .engineTimeout:
            return json(["ok": false, "message": "连接超时，请检查网络后重试"])
        case .engineAuthError:
            return json(["ok": false, "message": "鉴权失败：API key 无效"])
        default:
            return json(["ok": true, "message": "验证成功"])
        }
    }

    // 字段与真实后端对齐:url_jobs 同时带 status/stage/error/job_id/url（消费方读 status）。
    private func urlJobsFixture() -> [[String: Any]] {
        let base: [String: Any] = ["job_id": "urljob-mock", "url": "https://youtu.be/mock"]
        switch failure {
        case .youtubeNoTranscript:
            return [base.merging(["status": "failed", "stage": "parsing", "error": "该视频没有可用字幕（或地区受限）"]) { _, b in b }]
        case .llmNoKey:
            return [base.merging(["status": "failed", "stage": "dispatching", "error": "未配置 LLM key，请在「AI 引擎」页配置后重试"]) { _, b in b }]
        case .engineTimeout:
            return [base.merging(["status": "failed", "stage": "dispatching", "error": "处理超时，请稍后重试"]) { _, b in b }]
        case .engineAuthError:
            return [base.merging(["status": "failed", "stage": "dispatching", "error": "鉴权失败：API key 无效"]) { _, b in b }]
        default:
            return [base.merging(["status": "done", "stage": "done"]) { _, b in b }]
        }
    }

    private func podcastJobsFixture() -> [[String: Any]] {
        let base: [String: Any] = ["job_id": "podjob-mock", "title": "Mock Podcast"]
        switch failure {
        case .llmNoKey:
            return [base.merging(["status": "failed", "error": "未配置 LLM key，请在「AI 引擎」页配置后重试"]) { _, b in b }]
        case .engineTimeout:
            return [base.merging(["status": "failed", "error": "处理超时，请稍后重试"]) { _, b in b }]
        case .engineAuthError:
            return [base.merging(["status": "failed", "error": "鉴权失败：API key 无效"]) { _, b in b }]
        default:
            return [base.merging(["status": "done", "filename": "mock_podcast.wav"]) { _, b in b }]
        }
    }

    // MARK: - JSON helpers

    private func ok() -> (Int, Data?) { json(["ok": true]) }

    private func json(_ obj: Any) -> (Int, Data?) {
        let data = try? JSONSerialization.data(withJSONObject: obj)
        return (200, data)
    }
}

private extension String {
    /// 把 --mock-failure 的 snake_case 值转成 Failure 的 camelCase rawValue。
    var camelizedFailureKey: String {
        let parts = split(separator: "_")
        guard let first = parts.first else { return self }
        let rest = parts.dropFirst().map { $0.prefix(1).uppercased() + $0.dropFirst() }
        return ([String(first)] + rest).joined()
    }
}
