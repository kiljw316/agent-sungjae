"""macOS ``say`` TTS with single-flight playback (no overlapping speech) and an idle signal
the recorder waits on to avoid capturing our own voice (echo)."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from typing import Protocol


class Speaker(Protocol):
    def say(self, text: str) -> None: ...
    def wait_idle(self, timeout: float | None = None) -> None: ...
    def stop(self) -> None: ...


def korean_voices() -> list[str]:
    if not shutil.which("say"):
        return []
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=False).stdout
    voices = []
    for line in out.splitlines():
        if "ko_KR" in line:
            m = re.match(r"^(.+?)\s{2,}ko_KR", line)
            if m:
                voices.append(m.group(1).strip())
    return voices


def pick_voice(preferred: str) -> str | None:
    voices = korean_voices()
    if not voices:
        return None
    for v in voices:
        if v == preferred or v.startswith(preferred + " "):
            return v
    return voices[0]


def _clean_for_speech(text: str) -> str:
    return text.replace("※", "참고로,").replace("→", "에서").replace("#", " ")


class MacSpeaker:
    def __init__(self, voice: str | None, rate: int = 190) -> None:
        self.voice = voice
        self.rate = rate
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._proc: subprocess.Popen[bytes] | None = None
        self._last: tuple[str, float] = ("", 0.0)

    def say(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        now = time.monotonic()
        if text == self._last[0] and now - self._last[1] < 2.0:
            return  # duplicate (e.g. same event delivered twice) → skip
        self._last = (text, now)
        with self._lock:  # serialize: never two `say` processes at once
            self._idle.clear()
            try:
                cmd = ["say", "-r", str(self.rate)]
                if self.voice:
                    cmd += ["-v", self.voice]
                self._proc = subprocess.Popen([*cmd, _clean_for_speech(text)])
                self._proc.wait()
            finally:
                self._proc = None
                time.sleep(0.25)  # let speaker output decay before the mic opens
                self._idle.set()

    def wait_idle(self, timeout: float | None = None) -> None:
        self._idle.wait(timeout)

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


class SilentSpeaker:
    """Used with --no-tts and in tests; records what would have been spoken."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def say(self, text: str) -> None:
        self.spoken.append(text)

    def wait_idle(self, timeout: float | None = None) -> None:
        return None

    def stop(self) -> None:
        return None
