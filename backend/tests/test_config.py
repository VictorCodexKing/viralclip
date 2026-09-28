import importlib

import app.config as config_module


def _fresh_config(monkeypatch, env):
    # Clear all relevant vars first so the host environment cannot leak in.
    for key in [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_API_KEY",
        "LLM",
        "ASSEMBLY_AI_API_KEY",
        "WHISPER_MODEL",
        "WHISPER_MODEL_SIZE",
        "TRANSCRIPTION_PROVIDER",
        "PEXELS_API_KEY",
        "MAX_CLIPS",
        "CLIP_DURATION",
        "MAX_VIDEO_DURATION",
        "OUTPUT_DIR",
        "TEMP_DIR",
        "CORS_ORIGINS",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config_module)
    return config_module.Config()


def test_defaults(monkeypatch):
    cfg = _fresh_config(monkeypatch, {})
    assert cfg.whisper_model == "base"
    assert cfg.transcription_provider == "whisper"
    assert cfg.output_dir == "data/outputs"
    assert cfg.temp_dir == "data/temp"
    assert cfg.max_clips == 7
    assert cfg.max_video_duration == 5400
    assert cfg.cors_origins == ["http://localhost:3000"]


def test_default_llm_is_real_gemini_id(monkeypatch):
    cfg = _fresh_config(monkeypatch, {})
    assert cfg.llm == "google-gla:gemini-1.5-flash"
    # The reference project's fictional id must never be used.
    assert "gemini-3-flash-preview" not in cfg.llm


def test_llm_inferred_from_google_key(monkeypatch):
    cfg = _fresh_config(monkeypatch, {"GOOGLE_API_KEY": "gkey"})
    assert cfg.llm == "google-gla:gemini-1.5-flash"


def test_llm_inferred_from_openai_key(monkeypatch):
    cfg = _fresh_config(monkeypatch, {"OPENAI_API_KEY": "okey"})
    assert cfg.llm.startswith("openai:")


def test_llm_env_override_wins(monkeypatch):
    cfg = _fresh_config(
        monkeypatch, {"GOOGLE_API_KEY": "gkey", "LLM": "ollama:llama3"}
    )
    assert cfg.llm == "ollama:llama3"


def test_transcription_provider_normalization(monkeypatch):
    assert (
        _fresh_config(monkeypatch, {"TRANSCRIPTION_PROVIDER": "AssemblyAI"}).transcription_provider
        == "assemblyai"
    )
    assert (
        _fresh_config(monkeypatch, {"TRANSCRIPTION_PROVIDER": "youtube-captions"}).transcription_provider
        == "youtube_captions"
    )
    assert (
        _fresh_config(monkeypatch, {"TRANSCRIPTION_PROVIDER": "garbage"}).transcription_provider
        == "whisper"
    )


def test_cors_origins_csv(monkeypatch):
    cfg = _fresh_config(
        monkeypatch, {"CORS_ORIGINS": "http://a.com, http://b.com"}
    )
    assert cfg.cors_origins == ["http://a.com", "http://b.com"]


def test_int_overrides(monkeypatch):
    cfg = _fresh_config(monkeypatch, {"MAX_CLIPS": "3", "MAX_VIDEO_DURATION": "600"})
    assert cfg.max_clips == 3
    assert cfg.max_video_duration == 600
