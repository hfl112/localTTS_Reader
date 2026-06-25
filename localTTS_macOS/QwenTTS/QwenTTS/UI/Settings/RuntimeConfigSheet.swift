import SwiftUI

struct RuntimeConfigRow: View {
    let label: String
    @Binding var path: String
    let isInvalid: Bool
    
    var body: some View {
        HStack {
            Text(label)
                .frame(width: 140, alignment: .trailing)
            
            TextField("", text: $path)
                .textFieldStyle(.roundedBorder)
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(isInvalid ? Color.red : Color.clear, lineWidth: 1)
                )
            
            Button(action: {
                let panel = NSOpenPanel()
                panel.canChooseFiles = true
                panel.canChooseDirectories = true
                panel.allowsMultipleSelection = false
                if panel.runModal() == .OK {
                    if let url = panel.url {
                        path = url.path
                    }
                }
            }) {
                Image(systemName: "folder.fill")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
            .help("Browse...")
        }
    }
}

struct RuntimeConfigSheet: View {
    @Environment(\.dismiss) var dismiss
    
    @State private var pythonPath = "/Users/funanhe/00_MyCode/TTS/.venv/bin/python"
    @State private var backendPath = "/Users/funanhe/00_MyCode/TTS/localTTS_macOS/backend/app.py"
    @State private var mlxAudioDir = "/Users/funanhe/00_MyCode/TTS/mlx_audio"
    @State private var modelsDir = "/Users/funanhe/00_MyCode/TTS/mlx_audio/models"
    @State private var refAudioDir = "/Users/funanhe/00_MyCode/TTS/mlx_audio/reference"
    @State private var ffmpegPath = "/opt/homebrew/bin/ffmpeg"
    
    // Mock Validation States
    @State private var invalidPaths: Set<String> = []
    @State private var errorMessage: String? = "Permission denied for Models directory."
    @State private var showSavedSuccess = false
    
    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Advanced Runtime Environment")
                        .font(.headline)
                    Text("Configure the underlying engine dependencies. Incorrect paths will prevent the app from reading.")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
            .padding(20)
            
            Divider()
            
            // Warning banner
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundColor(.orange)
                Text("Changes to these paths will require a restart of the backend engine.")
                    .font(.system(size: 12))
                    .foregroundColor(.primary)
                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .background(Color.orange.opacity(0.1))
            
            // Error banner
            if let error = errorMessage {
                HStack(spacing: 8) {
                    Image(systemName: "xmark.octagon.fill")
                        .foregroundColor(.red)
                    Text(error)
                        .font(.system(size: 12))
                        .foregroundColor(.red)
                    Spacer()
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
                .background(Color.red.opacity(0.1))
            }
            
            // Form
            VStack(spacing: 16) {
                RuntimeConfigRow(label: "Python Executable", path: $pythonPath, isInvalid: invalidPaths.contains("python"))
                RuntimeConfigRow(label: "Backend Script", path: $backendPath, isInvalid: invalidPaths.contains("backend"))
                RuntimeConfigRow(label: "MLX Audio Directory", path: $mlxAudioDir, isInvalid: invalidPaths.contains("mlx"))
                RuntimeConfigRow(label: "Models Directory", path: $modelsDir, isInvalid: true) // explicitly showing error for mock
                RuntimeConfigRow(label: "Reference Audio", path: $refAudioDir, isInvalid: invalidPaths.contains("ref"))
                RuntimeConfigRow(label: "FFmpeg Path", path: $ffmpegPath, isInvalid: invalidPaths.contains("ffmpeg"))
            }
            .padding(24)
            
            Spacer()
            
            Divider()
            
            // Footer
            HStack {
                Button("Reset to Default") {
                    // Mock reset
                    pythonPath = "/usr/bin/python3"
                    errorMessage = nil
                }
                
                if showSavedSuccess {
                    Text("Saved successfully.")
                        .font(.system(size: 12))
                        .foregroundColor(.green)
                        .transition(.opacity)
                }
                
                Spacer()
                
                Button("Cancel") {
                    dismiss()
                }
                .keyboardShortcut(.cancelAction)
                
                Button("Save") {
                    // Mock save
                    showSavedSuccess = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                        dismiss()
                    }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
            .padding(20)
            .background(Color(NSColor.controlBackgroundColor))
        }
        .frame(width: 600, height: 500)
    }
}
