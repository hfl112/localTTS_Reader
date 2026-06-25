import SwiftUI
import AppKit

// A true macOS Settings-style row where label is on the left, right aligned.
struct SettingsRow<Content: View>: View {
    let title: String
    let content: Content
    
    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }
    
    var body: some View {
        HStack(alignment: .center, spacing: 16) {
            Text(title)
                .frame(width: 140, alignment: .trailing)
                .foregroundColor(.primary)
            
            content
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 4)
    }
}

struct SettingsCard<Content: View>: View {
    let title: String
    let content: Content
    
    init(title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }
    
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title)
                .font(.headline)
                .foregroundColor(.primary)
            
            VStack(spacing: 8) {
                content
            }
            .padding()
            .background(.regularMaterial)
            .cornerRadius(12)
            .shadow(color: Color.black.opacity(0.04), radius: 8, x: 0, y: 2)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
            )
        }
    }
}

struct SettingsView: View {
    @State private var showingRuntimeConfig = false
    @State private var showAdvanced = false
    
    // States
    @State private var defaultVoice = "Serena"
    @State private var performanceMode = "Balanced"
    @State private var batteryPolicy = true
    
    @State private var temperature = 0.2
    @State private var topP = 0.5
    @State private var repPenalty = 1.1
    @State private var seed = "42"
    
    // Download States
    @State private var qwen17BDownloading = false
    @State private var qwen17BProgress: Double = 0.0
    @State private var qwen17BError: String? = nil
    
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 32) {
                
                // General Card
                SettingsCard(title: "General") {
                    SettingsRow("Default Voice:") {
                        Picker("", selection: $defaultVoice) {
                            Text("Serena").tag("Serena")
                            Text("Ryan").tag("Ryan")
                        }
                        .pickerStyle(.menu)
                        .frame(width: 160)
                    }
                    
                    SettingsRow("Performance Mode:") {
                        Picker("", selection: $performanceMode) {
                            Text("Fast").tag("Fast")
                            Text("Balanced").tag("Balanced")
                            Text("Quiet").tag("Quiet")
                        }
                        .pickerStyle(.menu)
                        .frame(width: 160)
                    }
                    
                    SettingsRow("Battery Policy:") {
                        Toggle("Pause background generation on battery", isOn: $batteryPolicy)
                    }
                }
                
                // Local Model Card
                SettingsCard(title: "Local Model") {
                    // Qwen 1.7B
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("Qwen3-TTS-1.7B-8bit")
                                .fontWeight(.medium)
                            
                            Text("Not Installed")
                                .font(.system(size: 11, weight: .semibold))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.orange.opacity(0.1))
                                .foregroundColor(.orange)
                                .cornerRadius(4)
                            
                            Spacer()
                            
                            Button(qwen17BDownloading ? "Cancel" : "Download") {
                                if qwen17BDownloading {
                                    qwen17BDownloading = false
                                } else {
                                    qwen17BDownloading = true
                                    qwen17BProgress = 0.0
                                    qwen17BError = nil
                                    // mock download
                                    Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { timer in
                                        if !qwen17BDownloading { timer.invalidate(); return }
                                        qwen17BProgress += 0.02
                                        if qwen17BProgress >= 1.0 {
                                            qwen17BDownloading = false
                                            timer.invalidate()
                                        }
                                    }
                                }
                            }
                            .help("下载模型")
                        }
                        
                        if qwen17BDownloading {
                            HStack {
                                ProgressView(value: qwen17BProgress)
                                    .progressViewStyle(.linear)
                                    .frame(maxWidth: 200)
                                Text("\(Int(qwen17BProgress * 100))%")
                                    .font(.system(size: 11))
                                    .foregroundColor(.secondary)
                            }
                        }
                        if let error = qwen17BError {
                            Text(error)
                                .font(.system(size: 11))
                                .foregroundColor(.red)
                        }
                    }
                    .padding(.vertical, 4)
                    
                    Divider()
                    
                    // Qwen 0.6B
                    HStack {
                        Text("Qwen3-TTS-0.6B")
                            .fontWeight(.medium)
                        
                        Text("Installed")
                            .font(.system(size: 11, weight: .semibold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.green.opacity(0.1))
                            .foregroundColor(.green)
                            .cornerRadius(4)
                        
                        Spacer()
                        
                        Button("Redownload") {}
                            .help("重新下载模型")
                    }
                    .padding(.vertical, 4)
                }
                
                // Advanced Card
                SettingsCard(title: "Advanced Engine") {
                    DisclosureGroup("Advanced Parameters", isExpanded: $showAdvanced) {
                        VStack(spacing: 12) {
                            SettingsRow("Temperature:") {
                                HStack {
                                    Slider(value: $temperature, in: 0...1)
                                        .frame(width: 150)
                                    Text(String(format: "%.2f", temperature))
                                        .monospacedDigit()
                                        .frame(width: 40, alignment: .leading)
                                }
                            }
                            SettingsRow("Top P:") {
                                HStack {
                                    Slider(value: $topP, in: 0...1)
                                        .frame(width: 150)
                                    Text(String(format: "%.2f", topP))
                                        .monospacedDigit()
                                        .frame(width: 40, alignment: .leading)
                                }
                            }
                            SettingsRow("Rep. Penalty:") {
                                HStack {
                                    Slider(value: $repPenalty, in: 0...2)
                                        .frame(width: 150)
                                    Text(String(format: "%.2f", repPenalty))
                                        .monospacedDigit()
                                        .frame(width: 40, alignment: .leading)
                                }
                            }
                            SettingsRow("Seed:") {
                                TextField("", text: $seed)
                                    .textFieldStyle(.roundedBorder)
                                    .frame(width: 100)
                            }
                        }
                        .padding(.top, 12)
                    }
                    .help("高级参数")
                }
                
                // Runtime Config
                SettingsCard(title: "Environment") {
                    SettingsRow("Runtime Paths:") {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Advanced configuration for Python, MLX, and system binaries.")
                                .foregroundColor(.secondary)
                                .font(.system(size: 12))
                            Button("Configure...") {
                                showingRuntimeConfig = true
                            }
                            .help("运行环境配置")
                            .padding(.top, 4)
                        }
                    }
                }
                
            }
            .padding(40)
            .padding(.bottom, 60) // Extra padding to allow smooth scrolling to the bottom
            .frame(maxWidth: 750, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clear)
        .sheet(isPresented: $showingRuntimeConfig) {
            RuntimeConfigSheet()
        }
    }
}

class SettingsHostingController: NSHostingController<SettingsView> {
    weak var coordinator: ApplicationCoordinator?
    
    init(coordinator: ApplicationCoordinator?) {
        self.coordinator = coordinator
        super.init(rootView: SettingsView())
    }
    
    @MainActor required dynamic init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }
}
