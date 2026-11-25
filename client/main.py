"""Entry point: ``python -m client.main [--text] [--url URL]``."""

from __future__ import annotations

import argparse
import sys

from client.api.client import CookingApiClient
from client.app import CookingAssistantApp
from client.audio.tts import (
    MacSpeaker,
    SilentSpeaker,
    Speaker,
    korean_voices,
    pick_voice,
)
from client.config import ClientSettings
from client.inputs import InputSource, TextInput, VoiceInput
from client.ui.console import Console


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Voice Cooking Assistant (MacBook CLI)")
    p.add_argument("--server", help="FastAPI server URL (default: COOKING_CLIENT_SERVER_URL or http://127.0.0.1:8000)")
    p.add_argument("--url", help="recipe URL to import immediately")
    p.add_argument("--resume", metavar="SESSION_ID", help="resume a specific session")
    p.add_argument("--text", action="store_true", help="keyboard input only (no microphone / STT)")
    p.add_argument("--no-tts", action="store_true", help="do not speak responses")
    p.add_argument("--voice", help="macOS say voice (default Yuna)")
    p.add_argument("--stt-model", help="faster-whisper model size (tiny/base/small/medium)")
    p.add_argument("--list-voices", action="store_true", help="print available Korean voices and exit")
    return p.parse_args(argv)


def build_speaker(args: argparse.Namespace, settings: ClientSettings, console: Console) -> Speaker:
    if args.no_tts:
        return SilentSpeaker()
    voice = pick_voice(args.voice or settings.tts_voice)
    if voice is None:
        console.error("한국어 macOS 음성을 찾지 못했어요 (시스템 설정 > 손쉬운 사용 > 읽기 및 말하기). 기본 음성으로 읽습니다.")
    else:
        console.info(f"TTS 음성: {voice}")
    return MacSpeaker(voice, settings.tts_rate)


def build_input(args: argparse.Namespace, settings: ClientSettings, speaker: Speaker, console: Console) -> InputSource:
    if args.text:
        return TextInput()
    try:
        from client.audio.recorder import MicrophoneUnavailable, PushToTalkRecorder
        from client.audio.stt import WhisperTranscriber
    except ImportError as exc:
        console.error(f"음성 의존성이 없습니다 ({exc}). 'uv sync --extra voice' 후 다시 실행하거나 --text 를 쓰세요. 텍스트 모드로 전환합니다.")
        return TextInput()
    try:
        recorder = PushToTalkRecorder(settings.sample_rate, settings.max_record_seconds, settings.silence_stop_seconds)
        device = recorder.default_input_device()
        if device is None:
            raise MicrophoneUnavailable("입력 장치가 없습니다")
        console.info(f"마이크: {device}")
        model = args.stt_model or settings.stt_model
        console.info(f"STT 모델 로딩 중 (faster-whisper {model}, 첫 실행 시 다운로드)...")
        transcriber = WhisperTranscriber(model, settings.stt_compute_type)
    except Exception as exc:  # noqa: BLE001 - any audio init failure → text fallback
        console.error(f"음성 입력을 준비하지 못했어요: {exc}. 텍스트 모드로 전환합니다.")
        return TextInput()
    return VoiceInput(recorder, transcriber, speaker, console)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_voices:
        print("\n".join(korean_voices()) or "(한국어 음성 없음)")
        return 0
    settings = ClientSettings()
    console = Console()
    speaker = build_speaker(args, settings, console)
    inputs = build_input(args, settings, speaker, console)
    api = CookingApiClient.connect(args.server or settings.server_url, settings.request_timeout_seconds)
    app = CookingAssistantApp(api, speaker, inputs, console, settings.poll_interval_seconds)
    try:
        return app.run(url=args.url, resume=args.resume)
    except KeyboardInterrupt:
        speaker.stop()
        console.info("\n중단했습니다. 세션은 저장되어 있어요.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
