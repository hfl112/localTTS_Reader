import Foundation

struct Snapshot: Codable {
    // 现有 7 个字段保持不动
    var main_title: String?
    var main_progress: String?
    var main_is_playing: Bool?
    var is_paused: Bool?
    var playback_status: String?        // ADR-003: single computed playback truth
    var status_code: String?            // "IDLE"/"BUSY"/"COOLING"
    var current_article_chunks: [String]?
    var current_article_index: Int?

    // 新增字段：全部 Optional，缺失/多余都不会导致 JSONDecoder 失败
    // 注意：podcast_jobs（对象数组）刻意不纳入，播客任务列表走 fetchPodcastJobs() 专门端点
    var current_podcast_file: String?
    var current_playing_md5: String?
    var podcast_generation_paused: Bool?
    var podcast_generation_pause_reason: String?
    var active_podcast_processes: Int?
    var active_url_tasks: [String]?
    var instance_id: String?
    var last_active_time: Double?
}

struct SettingsModel: Codable {
    var model: String?
    var voice: String?
    var instruct: String?
    var temperature: Double?
    var top_p: Double?
    var top_k: Int?
    var seed: Int?
    var repetition_penalty: Double?
    var lang_code: String?
    var speed: Double?
    var performance_profile: String?
    var battery_podcast_policy: String?
    var extension_pairing_token: String?
}

// MARK: - AI 引擎 / 翻译配置
// 字段全部 Optional，宽松解析；tiers 这页不编辑，读出后原样回传以免丢字段。

struct EngineTranslateConfig: Codable {
    var selected: String?           // 当前所选 MT 供应商：google / microsoft / deepl
    var target_lang: String?        // 目标语言代码：zh / en / ja ...
    var order: [String]?
    var microsoft_key: String?
    var microsoft_region: String?
    var deepl_key: String?
}

struct EngineLLMConfig: Codable {
    var selected: String?           // 当前所选 LLM 供应商：gemini / claude / openai / deepseek / local
    var order: [String]?
    var keys: [String: String]?
    var local_model_path: String?
    // tiers 形状不固定，仅用于原样读出/回传，不在 UI 编辑
    var tiers: [String: [String: String]]?
}

struct EngineConfig: Codable {
    var translate: EngineTranslateConfig?
    var llm: EngineLLMConfig?
}

// MARK: - 引擎连通性检测返回
// POST /engines/check 的响应：{ "ok": Bool, "message": String }
struct EngineCheckResult: Codable {
    var ok: Bool
    var message: String?
}

struct SavedItem: Codable {
    var text: String
    var source: String
    var voice: String?
    var title: String?
    var timestamp: Double?
    var display_title: String?
}

struct UrlJob: Codable {
    var url: String
    var status: String
    var error: String?
    var title: String?
}

struct PodcastJob: Codable {
    var title: String
    var status: String
    var error: String?
}

struct PodcastFile: Codable {
    var filename: String
    var date: String
    var size: String
    var is_pinned: Bool
}

struct CacheItem: Codable {
    var md5: String
    var text_preview: String
    var date: String
    var duration: String
}
