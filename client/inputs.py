"""Where user utterances come from: keyboard, push-to-talk voice, or a script (tests)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol

from client.audio.tts import Speaker
from client.ui.console import Console


class EndOfInput(Exception):
    pass


class InputSource(Protocol):
    def read(self, prompt: str) -> str: ...


class TextInput:
    def read(self, prompt: str) -> str:
        try:
            return input(f"{prompt}\n> ").strip()
        except EOFError as exc:
            raise EndOfInput from exc


class ScriptedInput:
    def __init__(self, lines: Iterable[str], console: Console | None = None) -> None:
        self._it: Iterator[str] = iter(lines)
        self.console = console

    def read(self, prompt: str) -> str:
        try:
            line = next(self._it)
        except StopIteration as exc:
            raise EndOfInput from exc
        if self.console:
            if prompt:
                self.console.info(prompt)
            self.console.user(line)
        return line


class VoiceInput:
    """Push-to-talk: Enter → record → Enter/silence → transcribe. Typing text instead also works."""

    def __init__(self, recorder: object, transcriber: object, speaker: Speaker, console: Console) -> None:
        self.recorder = recorder
        self.transcriber = transcriber
        self.speaker = speaker
        self.console = console

    def read(self, prompt: str) -> str:
        while True:
            self.speaker.wait_idle()  # echo guard: never open the mic while TTS is playing
            try:
                typed = input(f"{prompt}  [Enter=말하기 · 글자 입력=텍스트 · q=종료]\n> ").strip()
            except EOFError as exc:
                raise EndOfInput from exc
            if typed:
                return typed
            self.console.info("🎙  듣는 중... (말을 마치면 Enter, 또는 잠시 멈추면 자동 종료)")
            audio = self.recorder.record()  # type: ignore[attr-defined]
            self.console.info("인식 중...")
            result = self.transcriber.transcribe(audio)  # type: ignore[attr-defined]
            if not result.text:
                self.console.error("음성을 인식하지 못했어요. 다시 말씀해 주세요.")
                continue
            self.console.user(f"{result.text}" + ("" if result.reliable else "  (인식 신뢰도 낮음)"))
            if not result.reliable:
                ok = input("이대로 보낼까요? [Enter=보내기 / r=다시 녹음 / 직접 입력]\n> ").strip()
                if ok.lower() == "r":
                    continue
                if ok:
                    return ok
            return result.text
