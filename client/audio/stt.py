"""Local Korean speech-to-text with faster-whisper (CPU int8 on Apple Silicon)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Whisper is known to hallucinate these on near-silent input.
_HALLUCINATIONS = ("시청해주셔서 감사합니다", "구독과 좋아요", "MBC 뉴스", "감사합니다.")


@dataclass
class Transcript:
    text: str
    avg_logprob: float
    no_speech_prob: float

    @property
    def reliable(self) -> bool:
        return bool(self.text) and self.no_speech_prob < 0.6 and self.avg_logprob > -1.2


class WhisperTranscriber:
    def __init__(self, model_size: str = "small", compute_type: str = "int8") -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_size, device="cpu", compute_type=compute_type)

    def transcribe(self, audio: np.ndarray | str, sample_rate: int = 16000) -> Transcript:
        if isinstance(audio, np.ndarray) and audio.size < sample_rate * 0.3:
            return Transcript("", -10.0, 1.0)
        segments, _info = self.model.transcribe(
            audio,
            language="ko",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt="요리 비서에게 하는 말: 다음 단계, 다시 설명해 줘, 고추 빼줘, 양파 대신 대파, 3분 타이머 시작.",
        )
        segs = list(segments)
        text = " ".join(s.text.strip() for s in segs).strip()
        if not segs or any(h in text for h in _HALLUCINATIONS) and len(text) < 20:
            return Transcript("", -10.0, 1.0)
        avg = sum(s.avg_logprob for s in segs) / len(segs)
        nsp = max(s.no_speech_prob for s in segs)
        return Transcript(text, avg, nsp)
