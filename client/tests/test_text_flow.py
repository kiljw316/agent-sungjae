"""#13 STT 없이 텍스트 입력으로 전체 플로우 실행 (client → real FastAPI app in-process)."""

from __future__ import annotations

import io
from collections.abc import Callable

from fastapi.testclient import TestClient

from client.api.client import CookingApiClient
from client.app import CookingAssistantApp
from client.audio.tts import SilentSpeaker
from client.inputs import ScriptedInput
from client.ui.console import Console
from server.app.main import create_app
from server.tests.support import RECIPE_URL


def run_client(tc: TestClient, lines: list[str]) -> tuple[int, SilentSpeaker, str, CookingAssistantApp]:
    out = io.StringIO()
    console = Console(out)
    speaker = SilentSpeaker()
    app = CookingAssistantApp(CookingApiClient(tc), speaker, ScriptedInput(lines, console), console, poll_events=False)
    code = app.run()
    return code, speaker, out.getvalue(), app


def test_full_text_flow(make_container: Callable, clock) -> None:  # type: ignore[no-untyped-def]
    script = [
        RECIPE_URL,
        "시작",
        "고추가 없는데 빼도 돼?",
        "응",
        "다음 단계",
        "양파 대신 대파를 넣을게",
        "응",
        "3분 타이머 시작",
        *["다음 단계"] * 7,
        "다음 단계",
    ]
    with TestClient(create_app(make_container())) as tc:
        code, speaker, out, app = run_client(tc, script)
    assert code == 0
    spoken = "\n".join(speaker.spoken)
    assert "이름: 제육볶음 만드는법" in out and "조리 단계:" in out
    assert speaker.spoken[0].startswith("제육볶음 만드는법 # 기사식당st 레시피 요리를 시작할게요. 총 9단계 중 1단계예요.")
    assert "레시피에서 고추를 제외할까요?" in spoken
    assert "고추를 제외하고 진행할게요" in spoken
    assert "양파 대신 대파를 쓰도록 바꿨어요" in spoken
    assert "당근 대파등을 먼저 넣어" in spoken  # step 5 guided with modified recipe
    assert "나머지 파등을넣어" in spoken  # step 8 without chili
    assert "바뀐 점은 2가지" in speaker.spoken[-1]
    assert app.session and app.session["status"] == "COMPLETED"
    assert "변경: 고추 제외" in out  # status line shows applied changes


def test_resume_after_client_restart_and_timer_event(make_container: Callable, clock) -> None:  # type: ignore[no-untyped-def]
    with TestClient(create_app(make_container())) as tc:
        run_client(tc, [RECIPE_URL, "시작", "다음 단계", "1분 타이머 시작"])  # input ends → client exits
        code, speaker, out, app = run_client(tc, ["응", "지금 몇 단계야?"])  # new client process
        assert "진행 중인 세션이 있어요" in out
        assert "2단계" in speaker.spoken[0]
        clock.advance(61)
        fired = app.poll_once()
        assert len(fired) == 1 and "타이머가 끝났어요" in speaker.spoken[-1]


def test_import_failure_offers_manual_text(make_container: Callable) -> None:
    import httpx

    transport = httpx.MockTransport(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(500))
    manual = ["제목: 수동 제육", "인분: 2", "[재료]", "돼지고기 600g", "양파 1개", "[조리]", "1. 고기를 볶는다", "2. 양파를 넣는다", "END"]
    with TestClient(create_app(make_container(transport=transport))) as tc:
        code, speaker, out, app = run_client(tc, [RECIPE_URL, "text", *manual, "시작", "다음", "다음"])
    assert "레시피를 가져오지 못했어요" in out
    assert "이름: 수동 제육" in out
    assert app.session and app.session["status"] == "COMPLETED"


def test_server_down_is_reported() -> None:
    import httpx

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    api = CookingApiClient(httpx.Client(base_url="http://127.0.0.1:9", transport=httpx.MockTransport(boom)))
    out = io.StringIO()
    console = Console(out)
    code = CookingAssistantApp(api, SilentSpeaker(), ScriptedInput([]), console, poll_events=False).run()
    assert code == 2 and "서버에 연결할 수 없습니다" in out.getvalue()
