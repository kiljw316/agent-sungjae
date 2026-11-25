"""Push-to-talk microphone capture: Enter starts, Enter (or trailing silence) stops."""

from __future__ import annotations

import queue
import select
import sys
import threading
import time
from collections.abc import Callable

import numpy as np


class MicrophoneUnavailable(Exception):
    pass


class PushToTalkRecorder:
    def __init__(self, sample_rate: int = 16000, max_seconds: float = 15.0, silence_stop_seconds: float = 1.5) -> None:
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self.silence_stop_seconds = silence_stop_seconds
        try:
            import sounddevice as sd  # noqa: F401
        except (ImportError, OSError) as exc:
            raise MicrophoneUnavailable(f"sounddevice/PortAudio를 불러올 수 없습니다: {exc}") from exc

    @staticmethod
    def default_input_device() -> str | None:
        import sounddevice as sd

        try:
            dev = sd.query_devices(kind="input")
        except Exception:  # noqa: BLE001 - PortAudio raises various errors
            return None
        return str(dev["name"]) if dev else None

    def record(self, on_level: Callable[[float], None] | None = None) -> np.ndarray:
        """Blocks until Enter, silence after speech, or max duration. Returns float32 mono."""
        import sounddevice as sd

        chunks: queue.Queue[np.ndarray] = queue.Queue()
        stop = threading.Event()

        def callback(indata: np.ndarray, frames: int, t: object, status: object) -> None:
            chunks.put(indata[:, 0].copy())

        frames: list[np.ndarray] = []
        speech_started = False
        last_voice = time.monotonic()
        noise_floor: float | None = None
        start = time.monotonic()
        try:
            stream = sd.InputStream(samplerate=self.sample_rate, channels=1, dtype="float32", callback=callback, blocksize=1600)
        except Exception as exc:  # noqa: BLE001
            raise MicrophoneUnavailable(f"마이크를 열 수 없습니다 (시스템 설정 > 개인정보 보호 > 마이크 권한 확인): {exc}") from exc
        with stream:
            while not stop.is_set():
                if select.select([sys.stdin], [], [], 0)[0]:
                    sys.stdin.readline()
                    break
                try:
                    chunk = chunks.get(timeout=0.1)
                except queue.Empty:
                    continue
                frames.append(chunk)
                rms = float(np.sqrt(np.mean(chunk**2)) + 1e-9)
                if on_level:
                    on_level(rms)
                elapsed = time.monotonic() - start
                if noise_floor is None or elapsed < 0.3:
                    noise_floor = rms if noise_floor is None else min(noise_floor * 0.9 + rms * 0.1, rms)
                    continue
                threshold = max(noise_floor * 3.0, 0.01)
                if rms > threshold:
                    speech_started = True
                    last_voice = time.monotonic()
                if (
                    speech_started
                    and self.silence_stop_seconds > 0
                    and time.monotonic() - last_voice > self.silence_stop_seconds
                ):
                    break
                if elapsed > self.max_seconds:
                    break
        return np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
