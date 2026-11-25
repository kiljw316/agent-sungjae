"""Terminal rendering. Pure presentation of server responses."""

from __future__ import annotations

import sys
import threading
from typing import Any, TextIO

_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


class Console:
    def __init__(self, out: TextIO | None = None) -> None:
        self.out = out or sys.stdout
        self._lock = threading.Lock()

    def print(self, text: str = "") -> None:
        with self._lock:
            print(text, file=self.out, flush=True)

    def info(self, text: str) -> None:
        self.print(_c("2", text))

    def error(self, text: str) -> None:
        self.print(_c("31", f"⚠ {text}"))

    def ai(self, text: str) -> None:
        self.print(_c("36;1", "AI: ") + text)

    def user(self, text: str) -> None:
        self.print(_c("33", "나: ") + text)

    def recipe(self, resp: dict[str, Any]) -> None:
        r = resp["recipe"]
        self.print()
        self.print(_c("1", f"이름: {r['title']}"))
        meta = []
        if r.get("servings"):
            meta.append(f"{r['servings']:g}인분")
        if r.get("totalTimeMinutes"):
            meta.append(f"약 {r['totalTimeMinutes']}분")
        meta.append(f"출처: {r.get('sourceUrl') or '직접 입력'} ({resp['parser']})")
        self.info(" · ".join(meta))
        self.print("재료:")
        for i in r["ingredients"]:
            self.print(f"  - {i['rawText']}")
        self.print("조리 단계:")
        for st in r["steps"]:
            self.print(f"  {st['position']}. {st['text']}")
        for w in resp.get("warnings", []):
            self.info(f"  ⓘ {w}")
        self.print()

    def status(self, s: dict[str, Any], timers: list[dict[str, Any]] | None = None) -> None:
        pos = s.get("currentStepPosition") or "-"
        bar = f"[세션 {s['sessionId'][:8]} | {s['status']} | 단계 {pos}/{s['totalSteps']} | v{s['version']}]"
        changes = [c["summary"] for c in s.get("appliedChanges", [])]
        line = _c("2", bar)
        if changes:
            line += _c("35", "  변경: " + "; ".join(changes))
        if timers:
            line += _c("32", "  ⏱ " + ", ".join(f"{t['label']} {t['remainingSeconds']}초" for t in timers))
        self.print(line)
