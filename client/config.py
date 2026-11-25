from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COOKING_CLIENT_", env_file=".env", extra="ignore")

    server_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: float = 30.0
    tts_voice: str = "Yuna"  # macOS Korean voice; falls back to any ko_KR voice
    tts_rate: int = 190
    stt_model: str = "small"  # faster-whisper size: tiny/base/small/medium. small ≈ 0.5GB, good Korean/latency balance on M-series
    stt_compute_type: str = "int8"
    sample_rate: int = 16000
    max_record_seconds: float = 15.0
    silence_stop_seconds: float = 1.5  # auto-stop after this much silence once speech started (0 = Enter only)
    poll_interval_seconds: float = 2.0
