"""#11 서버 재시작 후 세션/타이머 복원."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app.main import create_app
from server.tests.support import new_session, say


def test_session_and_timer_survive_restart(make_container, clock) -> None:  # type: ignore[no-untyped-def]
    with TestClient(create_app(make_container())) as c1:
        s = new_session(c1)
        sid = s["sessionId"]
        say(c1, sid, "다음 단계")
        say(c1, sid, "고추는 빼고 진행할게")
        say(c1, sid, "3분 타이머 시작")
        before = c1.get(f"/api/cooking-sessions/{sid}").json()

    clock.advance(60)  # server is "down" for a minute

    with TestClient(create_app(make_container())) as c2:  # fresh engine, same SQLite file
        after = c2.get(f"/api/cooking-sessions/{sid}").json()
        assert after == before
        assert c2.get("/api/cooking-sessions", params={"active": "true"}).json()[0]["sessionId"] == sid
        timers = c2.get(f"/api/cooking-sessions/{sid}/timers").json()
        assert timers[0]["status"] == "RUNNING" and timers[0]["remainingSeconds"] == 120
        r = say(c2, sid, "다음 단계")
        assert r["session"]["currentStepPosition"] == 3
