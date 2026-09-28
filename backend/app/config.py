"""Environment-driven configuration for the ViralClip backend.

Adapted (and heavily stripped) from the reference project's config: no
redis/db/auth/billing/ses/apify/self-host layers, no runtime admin settings.
Everything is read purely from environment variables via ``load_dotenv()``.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

_config_override: "Config | None" = None

LOCAL_OLLAMA_BASE_URL = "http://localhost:11434/v1"

# Real, currently-available default Gemini id (env ``LLM`` overrides it). The
# reference project shipped placeholder model ids that are not used here.
DEFAULT_LLM = "google-gla:gemini-1.5-flash"


class Config:
    def __init__(self) -> None:
        # --- LLM providers ---
        self.openai_api_key = self._get_optional_env("OPENAI_API_KEY")
        self.anthropic_api_key = self._get_optional_env("ANTHROPIC_API_KEY")
        self.google_api_key = self._get_optional_env("GOOGLE_API_KEY")
        self.ollama_base_url = self._get_optional_env("OLLAMA_BASE_URL")
        self.ollama_api_key = self._get_optional_env("OLLAMA_API_KEY")
        self.llm = self._get_optional_env("LLM") or self._infer_default_llm()

        # --- Transcription ---
        self.assembly_ai_api_key = self._get_optional_env("ASSEMBLY_AI_API_KEY")
        self.whisper_model = (
            os.getenv("WHISPER_MODEL") or os.getenv("WHISPER_MODEL_SIZE") or "base"
        )
        self.transcription_provider = self._normalize_transcription_provider(
            os.getenv("TRANSCRIPTION_PROVIDER", "whisper")
        )

        # --- B-roll ---
        self.pexels_api_key = self._get_optional_env("PEXELS_API_KEY")

        # --- Clipping limits ---
        self.max_clips = int(os.getenv("MAX_CLIPS", "7"))
        self.clip_duration = int(os.getenv("CLIP_DURATION", "30"))  # seconds
        self.max_video_duration = int(os.getenv("MAX_VIDEO_DURATION", "5400"))

        # --- Filesystem ---
        self.output_dir = os.getenv("OUTPUT_DIR", "data/outputs")
        self.temp_dir = os.getenv("TEMP_DIR", "data/temp")

        # --- HTTP ---
        self.cors_origins = self._get_csv_env(
            "CORS_ORIGINS", ["http://localhost:3000"]
        )

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _get_optional_env(name: str) -> str | None:
        value = os.getenv(name)
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _get_csv_env(name: str, default: list[str]) -> list[str]:
        value = os.getenv(name)
        if not value:
            return default
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _normalize_transcription_provider(value: str | None) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        if normalized in ("whisper", "assemblyai", "youtube_captions"):
            return normalized
        return "whisper"

    def resolve_ollama_base_url(self) -> str:
        return self.ollama_base_url or LOCAL_OLLAMA_BASE_URL

    def _infer_default_llm(self) -> str:
        """Infer a usable default model based on whichever API key is present.

        Falls back to Google for backward compatibility.
        """
        if self.google_api_key:
            return DEFAULT_LLM
        if self.openai_api_key:
            return "openai:gpt-4o-mini"
        if self.anthropic_api_key:
            return "anthropic:claude-3-5-sonnet-latest"
        return DEFAULT_LLM


def get_config() -> Config:
    override = _config_override
    if override is not None:
        return override
    return Config()


def set_config_override(config: "Config | None") -> None:
    global _config_override
    _config_override = config
