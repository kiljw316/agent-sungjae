"""Voice Cooking Assistant CLI flow. Business decisions are made by the server; this class only
collects input, forwards it, renders responses, speaks them, and announces timer events."""

from __future__ import annotations

import re
import threading
from typing import Any

from client.api.client import ApiError, CookingApiClient, ServerUnavailable
from client.audio.tts import Speaker
from client.inputs import EndOfInput, InputSource
from client.ui.console import Console

YES = re.compile(r"^(시작|응|네|예|그래|좋아|ㅇㅇ|y|yes|ok|start)", re.I)
QUIT = {"q", "quit", "exit", "종료하기"}
MANUAL_TEMPLATE = "제목: 제육볶음\n인분: 2\n[재료]\n돼지고기 600g\n양파 1개\n[조리]\n1. ...\n2. ..."


class CookingAssistantApp:
    def __init__(
        self, api: CookingApiClient, speaker: Speaker, inputs: InputSource, console: Console,
        poll_interval: float = 2.0, poll_events: bool = True,
    ) -> None:
        self.api = api
        self.speaker = speaker
        self.inputs = inputs
        self.console = console
        self.poll_interval = poll_interval
        self.poll_events = poll_events
        self.session: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._last_event_id = 0
        self._timers: list[dict[str, Any]] = []

    # ------------------------------------------------------------- output
    def respond(self, speech: str) -> None:
        self.console.ai(speech)
        self.speaker.say(speech)

    # ------------------------------------------------------------ startup
    def run(self, url: str | None = None, resume: str | None = None) -> int:
        self.console.print("Cooking Assistant Started.")
        try:
            health = self.api.health()
        except ServerUnavailable as exc:
            self.console.error(str(exc))
            self.console.info("서버를 먼저 실행하세요: uv run uvicorn server.app.main:app --port 8000")
            return 2
        self.console.info(f"서버 연결됨 · agent={health.get('agent')} · llm={health.get('llm')}")
        try:
            self.session = self._resume(resume) or self._new_session(url)
            if self.session is None:
                return 1
            self._start_poller()
            return self._loop()
        except EndOfInput:
            self.console.info("입력이 끝나 종료합니다. 진행 상태는 서버에 저장되어 다음 실행 때 이어서 할 수 있어요.")
            return 0
        finally:
            self._stop.set()

    def _resume(self, resume: str | None) -> dict[str, Any] | None:
        if resume:
            return self.api.get_session(resume)
        active = self.api.active_sessions()
        if not active:
            return None
        a = active[0]
        answer = self.inputs.read(
            f"진행 중인 세션이 있어요: {a['recipeTitle']} ({a['status']}, {a['currentStepPosition']}/{a['totalSteps']}단계). 이어서 할까요? (응/아니)"
        )
        if YES.match(answer):
            s = self.api.get_session(a["sessionId"])
            self.console.status(s)
            self.respond(self.api.command(s["sessionId"], "다시 설명해 줘")["speech"])
            return s
        return None

    def _new_session(self, url: str | None) -> dict[str, Any] | None:
        recipe = self._import(url)
        if recipe is None:
            return None
        self.console.recipe(recipe)
        answer = self.inputs.read("요리를 시작할까요?")
        if not YES.match(answer):
            self.console.info("요리를 시작하지 않고 종료합니다.")
            return None
        self.api.confirm_recipe(recipe["recipeId"])
        s = self.api.create_session(recipe["recipeId"])
        resp = self.api.command(s["sessionId"], "요리 시작", s["version"])
        self._render(resp)
        return resp["session"]

    def _import(self, url: str | None) -> dict[str, Any] | None:
        while True:
            target = url or self.inputs.read("레시피 URL을 입력하세요 (직접 입력은 'text', 저장된 레시피는 이름 검색):")
            url = None
            if target.lower() in QUIT:
                return None
            try:
                if target.lower() == "text":
                    return self._manual_import()
                if target.startswith("http"):
                    self.console.info("레시피를 가져오는 중...")
                    return self.api.import_url(target)
                found = self.api.find_recipes(re.sub(r"\s*(시작|해\s*줘|만들|요리).*$", "", target).strip() or None)
                if found:
                    self.console.info(f"저장된 레시피를 찾았어요: {found[0]['title']}")
                    return self.api.get_recipe(found[0]["recipeId"])
                self.console.error("저장된 레시피가 없어요. URL을 입력해 주세요.")
            except ApiError as exc:
                self.console.error(f"레시피를 가져오지 못했어요: {exc.message}")
                self.console.info("다른 URL을 입력하거나 'text'를 입력해 레시피를 직접 붙여 넣을 수 있어요.")

    def _manual_import(self) -> dict[str, Any] | None:
        self.console.print("레시피를 붙여 넣고, 마지막 줄에 'END'를 입력하세요. 예시:\n" + MANUAL_TEMPLATE)
        lines: list[str] = []
        while (line := self.inputs.read("")) != "END":
            lines.append(line)
        return self.api.import_text("\n".join(lines))

    # --------------------------------------------------------------- loop
    def _render(self, resp: dict[str, Any]) -> None:
        self.session = resp["session"]
        self.respond(resp["speech"])
        if resp.get("change") and resp["change"]["status"] == "PROPOSED":
            for e in resp["change"]["plan"]["stepEdits"]:
                if e["before"] != e["after"]:
                    self.console.info(f"   {e['position']}단계: {e['before']}\n        → {e['after']}")
        self.console.status(resp["session"], self._timers)

    def _loop(self) -> int:
        assert self.session is not None
        while self.session["status"] not in ("COMPLETED", "CANCELLED"):
            text = self.inputs.read("말씀하세요")
            if not text:
                continue
            if text.lower() in QUIT:
                self.console.info("클라이언트를 종료합니다. 세션은 저장되어 다음에 이어서 할 수 있어요.")
                return 0
            try:
                resp = self.api.command(self.session["sessionId"], text, self.session["version"])
            except ApiError as exc:
                if exc.code == "VERSION_CONFLICT":
                    self.session = self.api.get_session(self.session["sessionId"])
                    self.respond("다른 곳에서 진행 상태가 바뀌어 다시 불러왔어요. 한 번 더 말씀해 주세요.")
                else:
                    self.console.error(exc.message)
                continue
            except ServerUnavailable as exc:
                self.console.error(f"{exc} — 서버가 다시 켜지면 같은 명령을 다시 말씀해 주세요.")
                continue
            self._render(resp)
        self.console.print("요리 세션이 끝났습니다. 수고하셨어요!")
        return 0

    # ------------------------------------------------------------- events
    def _start_poller(self) -> None:
        if not self.poll_events or self.session is None:
            return
        threading.Thread(target=self._poll, name="event-poller", daemon=True).start()

    def poll_once(self) -> list[dict[str, Any]]:
        assert self.session is not None
        data = self.api.events(self.session["sessionId"], self._last_event_id)
        self._last_event_id = data["lastEventId"]
        self._timers = data["timers"]
        fired = [e for e in data["events"] if e["type"] == "TIMER_FINISHED"]
        for e in fired:
            self.console.print()
            self.respond(f"⏰ {e['message']}")
        return fired

    def _poll(self) -> None:
        assert self.session is not None
        try:  # skip historical events on (re)start
            self._last_event_id = self.api.events(self.session["sessionId"], 0)["lastEventId"]
        except (ApiError, ServerUnavailable):
            pass
        while not self._stop.wait(self.poll_interval):
            try:
                self.poll_once()
            except (ApiError, ServerUnavailable):
                continue
