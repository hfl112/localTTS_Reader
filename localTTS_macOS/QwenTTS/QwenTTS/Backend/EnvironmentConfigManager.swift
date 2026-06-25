import Foundation

enum EnvironmentMode: String, Codable {
    case builtin
    case custom
}

struct CustomEnvironmentConfig: Codable {
    var pythonPath: String
    var backendPath: String
    var mlxAudioPath: String
    var modelsPath: String
    var referenceAudioPath: String
    var ffmpegPath: String
}

class EnvironmentConfigManager {
    static let shared = EnvironmentConfigManager()
    
    private let modeKey = "tts_env_mode"
    private let configKey = "tts_env_custom_config"
    
    var mode: EnvironmentMode {
        get {
            guard let raw = UserDefaults.standard.string(forKey: modeKey),
                  let mode = EnvironmentMode(rawValue: raw) else {
                return .builtin
            }
            return mode
        }
        set {
            UserDefaults.standard.set(newValue.rawValue, forKey: modeKey)
        }
    }
    
    var customConfig: CustomEnvironmentConfig {
        get {
            if let data = UserDefaults.standard.data(forKey: configKey),
               let config = try? JSONDecoder().decode(CustomEnvironmentConfig.self, from: data) {
                return config
            }
            return CustomEnvironmentConfig(
                pythonPath: "",
                backendPath: "",
                mlxAudioPath: "",
                modelsPath: "",
                referenceAudioPath: "",
                ffmpegPath: ""
            )
        }
        set {
            if let data = try? JSONEncoder().encode(newValue) {
                UserDefaults.standard.set(data, forKey: configKey)
            }
        }
    }
    
    func resetToBuiltin() {
        mode = .builtin
    }
}
