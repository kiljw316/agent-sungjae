"""Real audio pipeline (opt-in: ``pytest --run-voice``): macOS `say` → audio file → faster-whisper.
Does not use the microphone; verifies TTS voice availability and Korean STT end to end."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from client.audio.tts import korean_voices, pick_voice

pytestmark = pytest.mark.voice


@pytest.fixture(scope="module")
def transcriber():  # type: ignore[no-untyped-def]
    from client.audio.stt import WhisperTranscriber

    return WhisperTranscriber("small")


def test_korean_voice_available() -> None:
    assert korean_voices(), "no ko_KR voice for `say`"
    assert pick_voice("Yuna")


@pytest.mark.parametrize("phrase", ["다음 단계", "고추는 빼고 진행할게", "양파 대신 대파를 넣을게", "3분 타이머 시작"])
def test_say_to_whisper_roundtrip(tmp_path: Path, transcriber, phrase: str) -> None:  # type: ignore[no-untyped-def]
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed to convert AIFF")
    aiff, wav = tmp_path / "a.aiff", tmp_path / "a.wav"
    subprocess.run(["say", "-v", pick_voice("Yuna") or "Yuna", "-o", str(aiff), phrase], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(wav)], check=True)
    result = transcriber.transcribe(str(wav))
    norm = lambda t: t.replace(" ", "").rstrip(".")  # noqa: E731
    assert norm(result.text) == norm(phrase), result
