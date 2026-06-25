# Native backend source

This directory is the independent Python backend source for `localTTS_macOS`.

- `core/` is the native App's backend implementation.
- `mlx_audio/mlx_audio/` is its MLX-Audio source snapshot.
- `URL-Reader/` is its URL processing source snapshot.
- `reference/` contains its bundled ICL reference audio.

Do not import, copy, or synchronize source code from `QwenTTS-App/` during a
normal native build. Changes made here must not be copied back automatically.
The legacy `QwenTTS-App/` remains frozen for daily use through `python app.py`.

Large model weights are intentionally not duplicated. Development may point
`TTS_MODELS_PATH` to an existing read-only model directory; release builds use
the native App's Application Support model directory.
